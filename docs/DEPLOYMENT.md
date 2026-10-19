# Deployment guide (AWS EC2 + GitHub Actions)

```
git push → test + evaluate → build image → smoke test → push to GHCR → SSH to EC2 → docker compose up -d
```

## One-time AWS setup
1. **Launch an EC2 instance:** Ubuntu 24.04, at least 8 GB RAM (for example `t3.large`; `t3.xlarge` is more comfortable), 30 GB disk. CPU-only inference is slow, so expect answers to take several seconds or more.
2. **Security group:** port 22 (SSH) from **your IP only**, port 80 (HTTP) from anywhere. Never open 11434 (Ollama).
3. **Create a key pair** and keep the private key.
4. **Install Docker and prepare the folder:**
   ```bash
   ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker ubuntu && exit            # log back in afterwards
   sudo mkdir -p /opt/paynest && sudo chown ubuntu /opt/paynest
   echo "API_KEY=choose-a-long-random-string" > /opt/paynest/.env
   ```
5. **Let the server pull your image.** Make the GitHub package public, or log in once on the server:
   ```bash
   echo <GITHUB_PAT_WITH_read:packages> | docker login ghcr.io -u <github-username> --password-stdin
   ```

## One-time GitHub setup
0. The deploy job is off by default. When you are ready, add the repository **variable** `DEPLOY_ENABLED` with the value `true` (Settings > Secrets and variables > Actions > Variables).
1. Push the project to a repository on the `main` branch.
2. **Settings > Secrets and variables > Actions**, add `EC2_HOST`, `EC2_USER` (`ubuntu`) and `EC2_SSH_KEY` (full private key contents).
3. Optional: **Settings > Environments > production** with a required reviewer so deploys need approval.

## Deploy
Push to `main`. The first deploy is slow because the server downloads the models.

## Verify
```bash
curl http://<EC2_PUBLIC_IP>/health
curl -X POST http://<EC2_PUBLIC_IP>/ask \
  -H "Content-Type: application/json" -H "X-API-Key: <your key>" \
  -d '{"question": "How long do instant transfers take?"}'
```
The chat UI is at `http://<EC2_PUBLIC_IP>/` (enter the API key under Settings).

## Roll back
```bash
cd /opt/paynest
export API_IMAGE=ghcr.io/<owner>/<repo>:<old-commit-sha>
docker compose -f docker-compose.prod.yml up -d
```

## Observability
- Logs: `docker compose -f docker-compose.prod.yml logs -f api` (one line per request with status and duration).
- Health: `GET /health` (Docker HEALTHCHECK; point a Route 53 health check or load balancer at it).
- Watch: `latency_ms`, 503 rate, CPU and memory in the EC2 console, disk usage (models are large).
- Next: ship logs to CloudWatch with the `awslogs` driver and add alarms.

## Hardening
- Put HTTPS in front (Caddy, or an Application Load Balancer with an ACM certificate).
- Keep `API_KEY` set and rotate it if leaked. Add rate limiting (for example `slowapi`).
- Swap GHCR for Amazon ECR if you want everything inside AWS (instance IAM role with ECR read access).
- Stop the instance when not in use: EC2 bills while it runs.

## Not verified by the author
The Docker build, Docker Compose, Ollama mode and the GitHub Actions and AWS steps were written and reviewed but not run in the development sandbox. Run them once and read the logs.
