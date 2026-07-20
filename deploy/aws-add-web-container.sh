#!/usr/bin/env bash
# One-time: add the wiki client (`web`) as a SECOND container in the existing
# manthana-server ECS task, and route ALB traffic to it.
#
# Run this once, by hand, before the first deploy that carries a `web` image.
# After it, .github/workflows/deploy-aws.yml only ever swaps images.
#
# ── Why the step order is the entire point ───────────────────────────────────
#
# Today the 443 listener has exactly ONE rule: default → the server target
# group. Every path reaches the server that way, including /v1/* — the sync
# endpoint every engineer's agent posts to. So the dangerous move is flipping
# the default to the client before the server's own paths are pinned by explicit
# rules: that would take the customer data pipeline down, not just the wiki.
#
# Steps 1-4 are therefore all inert or invisible to live traffic, and step 4 is
# what MAKES step 5 safe:
#
#   1  ECR repo for the client image            — nothing serves it yet
#   2  target group for :3000                   — no targets, no traffic
#   3  explicit /ui*, /v1*, ... → server rules  — a NO-OP today (default already
#                                                 sends these to the server), and
#                                                 the safety net for step 5
#   4  task revision + service gains 2nd LB     — client starts and goes healthy,
#      mapping                                    still receives no traffic
#   5  flip the listener default → client       — THE CUTOVER (needs --cutover)
#   6  retire the HTML wiki on the server        — needs --retire
#
# Steps 1-4 are idempotent and safe to re-run. Steps 5 and 6 change what users
# see and are opt-in flags, so a re-run of the setup can never cut over by
# accident.
#
# Rollback for step 5 is one command, printed when it runs.
#
# Usage:
#   ./deploy/aws-add-web-container.sh                 # steps 1-4 only
#   ./deploy/aws-add-web-container.sh --cutover       # + step 5
#   ./deploy/aws-add-web-container.sh --cutover --retire  # + step 6
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail

REGION=${AWS_REGION:-us-east-1}
ACCOUNT=086561632645
CLUSTER=manthana
SERVICE=manthana-server
FAMILY=manthana-server
REPO_WEB=manthana-web
ECR_WEB="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO_WEB"
SERVER_TG="arn:aws:elasticloadbalancing:$REGION:$ACCOUNT:targetgroup/manthana-app/250460c7c18f7c80"
LISTENER_443="arn:aws:elasticloadbalancing:$REGION:$ACCOUNT:listener/app/manthana-alb/aab1f836a26d4937/b885a743656d4353"
VPC=vpc-06405db9c7aac3676
WEB_PORT=3000

# Paths the SERVER owns. Everything else belongs to the client. Keep this in
# lockstep with deploy/Caddyfile — the two are the same routing decision made
# twice, once for the hosted stack and once for self-hosters.
SERVER_PATHS_1='/ui,/ui/*,/v1,/v1/*,/mcp'
SERVER_PATHS_2='/mcp/*,/docs,/docs/*,/openapi.json,/healthz'
SERVER_PATHS_3='/readyz'

CUTOVER=false
RETIRE=false
for arg in "$@"; do
  case "$arg" in
    --cutover) CUTOVER=true ;;
    --retire)  RETIRE=true ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done
$RETIRE && ! $CUTOVER && { echo "--retire requires --cutover" >&2; exit 2; }

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1. ECR repository ────────────────────────────────────────────────────────
say "1. ECR repository $REPO_WEB"
aws ecr describe-repositories --repository-names "$REPO_WEB" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO_WEB" --region "$REGION" \
       --image-scanning-configuration scanOnPush=true --query 'repository.repositoryUri' --output text
echo "ok: $ECR_WEB"

# The task cannot start without an image to pull, so the first one is built here
# rather than waiting for a tagged deploy.
if ! aws ecr describe-images --repository-name "$REPO_WEB" --image-ids imageTag=latest \
       --region "$REGION" >/dev/null 2>&1; then
  say "1b. seeding the first client image (arm64, to match the task's Graviton platform)"
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
  docker build --platform linux/arm64 -t "${ECR_WEB}:latest" "$(dirname "$0")/../web"
  docker push "${ECR_WEB}:latest"
fi

# ── 2. Target group for the client ───────────────────────────────────────────
say "2. target group manthana-web"
WEB_TG=$(aws elbv2 describe-target-groups --names manthana-web --region "$REGION" \
           --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if [ -z "$WEB_TG" ] || [ "$WEB_TG" = "None" ]; then
  # Health check hits /login: it is the one client route that renders without a
  # session, so a healthy check means "the app is serving", not "auth happens to
  # be configured".
  WEB_TG=$(aws elbv2 create-target-group --name manthana-web --protocol HTTP --port "$WEB_PORT" \
             --vpc-id "$VPC" --target-type ip --health-check-path /login \
             --matcher HttpCode=200 --region "$REGION" \
             --query 'TargetGroups[0].TargetGroupArn' --output text)
fi
echo "ok: $WEB_TG"

# ── 3. Pin the server's paths BEFORE anything can take the default ───────────
say "3. listener rules: server paths -> server target group"
existing=$(aws elbv2 describe-rules --listener-arn "$LISTENER_443" --region "$REGION" \
             --query 'Rules[?Priority!=`default`].Priority' --output text)
if [ -z "$existing" ]; then
  prio=10
  for paths in "$SERVER_PATHS_1" "$SERVER_PATHS_2" "$SERVER_PATHS_3"; do
    # Split across rules because an ALB path-pattern condition takes at most 5
    # values. Priority ordering is irrelevant between them — they are disjoint.
    aws elbv2 create-rule --listener-arn "$LISTENER_443" --priority "$prio" \
      --conditions "Field=path-pattern,Values=[$(echo "$paths" | sed 's/[^,]*/"&"/g')]" \
      --actions "Type=forward,TargetGroupArn=$SERVER_TG" \
      --region "$REGION" --query 'Rules[0].RuleArn' --output text
    prio=$((prio + 10))
  done
else
  echo "ok: rules already present ($existing)"
fi
echo "NOTE: behaviourally a no-op right now — the default still forwards to the"
echo "      server. These rules are what makes step 5 safe."

# ── 4. Add the web container + second load-balancer mapping ──────────────────
say "4. task definition: add the web container"
aws ecs describe-task-definition --task-definition "$FAMILY" --region "$REGION" \
  --query taskDefinition --output json > /tmp/manthana-taskdef.json

if jq -e 'any(.containerDefinitions[]; .name == "web")' /tmp/manthana-taskdef.json >/dev/null; then
  echo "ok: web container already present"
else
  # NOTE: no container-level healthCheck for `web`, deliberately. The ALB target
  # group already probes /login over HTTP and is what actually gates traffic; a
  # second, container-level probe would add no signal while adding a way to fail.
  # On an `essential` container a health-check command that is subtly wrong (the
  # wrong shell, a missing binary in the runtime image) kills the whole task in a
  # loop — including the server beside it. The server container keeps its own
  # check because that command is long-proven in this image.
  jq --arg img "${ECR_WEB}:latest" --argjson port "$WEB_PORT" --arg region "$REGION" '
      .containerDefinitions += [{
        name: "web",
        image: $img,
        # essential: a task serving the API with a dead client would look healthy
        # to ECS while every wiki page 502s.
        essential: true,
        portMappings: [{containerPort: $port, hostPort: $port, protocol: "tcp"}],
        environment: [
          {name: "NODE_ENV",               value: "production"},
          {name: "PORT",                   value: ($port|tostring)},
          {name: "NEXT_TELEMETRY_DISABLED", value: "1"}
        ],
        logConfiguration: {
          logDriver: "awslogs",
          options: {
            "awslogs-group": "/ecs/manthana-server",
            "awslogs-region": $region,
            "awslogs-stream-prefix": "web"
          }
        }
      }]
      | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
            .compatibilities, .registeredAt, .registeredBy)' \
    /tmp/manthana-taskdef.json > /tmp/manthana-taskdef-new.json
  # No cpu/memory bump: the task already reserves 2048 CPU / 8192 MiB and the
  # containers share it, so a Next.js server fits without resizing.
  aws ecs register-task-definition --cli-input-json file:///tmp/manthana-taskdef-new.json \
    --region "$REGION" --query 'taskDefinition.revision' --output text
fi

say "4b. service: second load-balancer mapping"
if aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION" \
     --query 'services[0].loadBalancers[?containerName==`web`]' --output text | grep -q web; then
  echo "ok: already mapped"
else
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$FAMILY" --region "$REGION" \
    --load-balancers \
      "targetGroupArn=$SERVER_TG,containerName=server,containerPort=8000" \
      "targetGroupArn=$WEB_TG,containerName=web,containerPort=$WEB_PORT" \
    --query 'service.deployments[0].id' --output text
fi
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION"
echo "ok: client is running and healthy, and still receives no traffic"

if ! $CUTOVER; then
  cat <<EOF

Stopping before the cutover. Nothing users can see has changed.

Verify the client is healthy, then re-run with --cutover:
  aws elbv2 describe-target-health --target-group-arn $WEB_TG --region $REGION
EOF
  exit 0
fi

# ── 5. THE CUTOVER ───────────────────────────────────────────────────────────
say "5. flipping the listener default -> client"
echo "Rollback (run this if anything looks wrong):"
echo "  aws elbv2 modify-listener --listener-arn $LISTENER_443 \\"
echo "    --default-actions Type=forward,TargetGroupArn=$SERVER_TG --region $REGION"
aws elbv2 modify-listener --listener-arn "$LISTENER_443" \
  --default-actions "Type=forward,TargetGroupArn=$WEB_TG" \
  --region "$REGION" --query 'Listeners[0].ListenerArn' --output text

# ── 6. Retire the HTML wiki ──────────────────────────────────────────────────
if $RETIRE; then
  say "6. retiring the server-rendered wiki"
  aws ecs describe-task-definition --task-definition "$FAMILY" --region "$REGION" \
    --query taskDefinition --output json > /tmp/manthana-taskdef.json
  jq '
      .containerDefinitions |= map(
        if .name == "server" then
          .environment = ((.environment // [])
            | map(select(.name != "MANTHANA_SERVER_RETIRE_HTML_WIKI"))
            + [{name: "MANTHANA_SERVER_RETIRE_HTML_WIKI", value: "1"}])
        else . end)
      | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
            .compatibilities, .registeredAt, .registeredBy)' \
    /tmp/manthana-taskdef.json > /tmp/manthana-taskdef-new.json
  aws ecs register-task-definition --cli-input-json file:///tmp/manthana-taskdef-new.json \
    --region "$REGION" --query 'taskDefinition.revision' --output text
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$FAMILY" --region "$REGION" --query 'service.deployments[0].id' --output text
  aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE" --region "$REGION"
fi

cat <<EOF

Done. Check, in this order:
  /healthz and /v1  -> still the server (agent sync must be unaffected)
  /                 -> the wiki client
  /ui               -> the founder console
EOF
