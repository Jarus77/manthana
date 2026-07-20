# Hosted AWS infrastructure (account 086561632645, us-east-1)

None of this is infrastructure-as-code. It was created by hand and is recorded
here so a rebuild — or an engineer debugging a deploy at 2am — does not have to
reverse-engineer it from the console. **If you change the live account, change
this file in the same commit.**

`deploy/aws-add-web-container.sh` reproduces the parts it created (§4 below).
Everything else, including the IAM policy in §5, exists only in the account.

## 1. Request path

```
internet
  └── ALB  manthana-alb  (internet-facing, HTTP :80 + HTTPS :443)
        listener 443, rules in priority order:
          10   /ui  /ui/*  /v1  /v1/*  /mcp            → TG manthana-app
          20   /mcp/*  /docs  /docs/*  /openapi.json  /healthz → TG manthana-app
          30   /readyz                                  → TG manthana-app
          default (weighted forward)                    → manthana-app 0%
                                                          manthana-web  100%
```

The **default action is a weighted forward**, not a plain one. That is
deliberate and load-bearing in two ways: a target group must appear in some
listener action before ECS will accept it in a service's `loadBalancers`, and
weights make the cutover (and its rollback) a single instant change rather than
a target swap. Shifting all traffic back to the server is one command:

```sh
aws elbv2 modify-listener --region us-east-1 \
  --listener-arn arn:aws:elasticloadbalancing:us-east-1:086561632645:listener/app/manthana-alb/aab1f836a26d4937/b885a743656d4353 \
  --default-actions '[{"Type":"forward","ForwardConfig":{"TargetGroups":[
    {"TargetGroupArn":"arn:aws:elasticloadbalancing:us-east-1:086561632645:targetgroup/manthana-app/250460c7c18f7c80","Weight":100},
    {"TargetGroupArn":"arn:aws:elasticloadbalancing:us-east-1:086561632645:targetgroup/manthana-web/a5013fa7c7702198","Weight":0}]}}]'
```

**The path rules are the safety net.** Every path the server owns is pinned by
an explicit rule, so the default can point anywhere without taking down the API.
Before those rules existed the single default rule carried everything — including
`/v1/*`, the endpoint every engineer's agent syncs to. Keep the rule list in
lockstep with `deploy/Caddyfile`; they are the same routing decision made twice,
once for the hosted stack and once for self-hosters.

## 2. Target groups

| | manthana-app | manthana-web |
|---|---|---|
| port | 8000 | 3000 |
| container | `server` | `web` |
| health check | `/readyz` | `/login` |
| target type | `ip` (required by awsvpc) | `ip` |

`/login` is the client's health path because it is the one route that renders
without a session — a pass means "the app is serving", not "auth happens to be
configured".

## 3. ECS

Cluster `manthana`, service `manthana-server`, **Fargate / awsvpc /
ARM64 (Graviton)**, 2048 CPU / 8192 MiB, desired count 1, VPC
`vpc-06405db9c7aac3676`.

Task family `manthana-server` runs **two containers in one task**:

| container | port | image |
|---|---|---|
| `server` | 8000 | `…/manthana-server:sha-<commit>` |
| `web` | 3000 | `…/manthana-web:sha-<commit>` |

They share a task on purpose. The client calls the server over same-origin
`/ui/api/wiki/*`, so a task pairing a new client with an old server (or the
reverse) is a broken wiki either way; one task definition revision means there is
no window in which the two disagree. Both carry the same image tag, which is the
only thing recording which builds were tested together.

Deployment config — the reason a bad deploy has never taken the site down:
`deploymentCircuitBreaker` **enabled with rollback**, `minimumHealthyPercent 100`
(the old task keeps serving until the new one is healthy), `maximumPercent 200`.
Note `healthCheckGracePeriodSeconds` is **0**: the ALB starts probing
immediately, so anything slow to become ready fails its first checks.

Logs: CloudWatch group `/ecs/manthana-server`, stream prefixes `server` and `web`.

Roles: `manthana-ecs-execution` (pulls images — carries the AWS-managed
`AmazonECSTaskExecutionRolePolicy`, which is why it can pull `manthana-web`
without a change) and `manthana-ecs-task`.

## 4. Security group — MANUAL, and reproduced by the setup script

Task SG `sg-065027ceba16ffcc3` admits the ALB SG `sg-0b57f2f41b082a39b` on:

- **8000** — the server. Original rule.
- **3000** — the client. **Added by hand 2026-07-20** (`sgr-0f99ce7bb3f39ed70`),
  and now also created by `deploy/aws-add-web-container.sh` step 2b.

Worth knowing because the failure it causes is actively misleading: without the
3000 rule the client can never pass its health check, and since ECS kills a task
that stays unhealthy in *any* registered target group, **the server dies with
it** — while the stop reason names the *server's* target group and the server's
own logs look perfectly healthy, because the request that is failing never
arrives.

## 5. IAM — MANUAL, and reproduced NOWHERE

`manthana-github-deploy` (assumed by GitHub Actions via OIDC; no stored AWS keys)
carries one inline policy, `push-image-roll-service`. Its `EcrPush` statement was
scoped to `manthana-server` alone; **on 2026-07-20 `manthana-web` was added by
hand** so `deploy-aws.yml` can push the client image:

```
"Resource": [
  "arn:aws:ecr:us-east-1:086561632645:repository/manthana-server",
  "arn:aws:ecr:us-east-1:086561632645:repository/manthana-web"
]
```

**This is the one change with no script and no code representation.** A rebuilt
role that copies the original policy will pass CI, build both images, and then
fail the deploy with `not authorized to perform: ecr:InitiateLayerUpload on
…/manthana-web`. Reapply with:

```sh
aws iam get-role-policy --role-name manthana-github-deploy \
  --policy-name push-image-roll-service --query PolicyDocument --output json > p.json
jq '(.Statement[]|select(.Sid=="EcrPush")|.Resource) |= ((if type=="array" then . else [.] end)
    + ["arn:aws:ecr:us-east-1:086561632645:repository/manthana-web"])' p.json > p2.json
aws iam put-role-policy --role-name manthana-github-deploy \
  --policy-name push-image-roll-service --policy-document file://p2.json
```

The policy keeps explicit per-repository resources rather than a wildcard; adding
a third image means adding a third ARN, not loosening the scope.

## 6. ECR

`manthana-server` and `manthana-web`. Both are pushed by `deploy-aws.yml` under
the same tag (`v*` tag name, else `sha-<12>`), plus `latest`.

## 7. Known gaps

- **No IaC.** Everything above was clicked or scripted by hand. The ALB, target
  groups, listener rules, service, roles, VPC and secrets have no source of
  truth outside this document.
- **Secrets** (`MANTHANA_SERVER_DB_URL`, `_JWT_SECRET`, `_ADMIN_TOKEN`,
  `ANTHROPIC_API_KEY`) are ECS secret references; their store and values are not
  recorded here on purpose.
- `healthCheckGracePeriodSeconds: 0` leaves no slack for a slow start. It has not
  bitten yet, but it is one long migration away from doing so.
