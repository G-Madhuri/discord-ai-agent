> [!NOTE]
> **Historical Archive**: This document contains the original Google Cloud Run deployment configuration.
> Active deployment configuration has migrated to Railway. See [`docs/railway-deploy.md`](../railway-deploy.md) for the active deployment guide.

# Cloud Run Deployment & Scaling Configuration (Historical)

## High Performance & Low Latency Scaling Configuration

To ensure sub-5-second execution for all agentic workflows, eliminate background task CPU starvation, and guarantee immediate Discord interaction handling, Cloud Run was deployed with both `--no-cpu-throttling` and `--min-instances=1`:

```bash
gcloud run services update discord-agent \
  --no-cpu-throttling \
  --min-instances=1 \
  --region=us-central1 \
  --project=discord-assignment-agent
```

### Infrastructure Rationale & Cost Trade-off
- **Cost:** ~$45/month (baseline allocated memory and continuous CPU for 1 dedicated warm container in `us-central1`).
- **Benefits:**
  1. **Zero Cold Starts:** Requests are served immediately without container spin-up delays.
  2. **Unthrottled Background Tasks:** Asynchronous tasks (LLM project planning, pgvector RAG document chunking/embedding, and bulk database commits) run at full CPU allocation after the HTTP interaction response (Type 5/6) returns to Discord.
  3. **Sub-5-Second Response Times:** Response times from modal submit to Discord summary PATCH drop to under 15 seconds total (LLM call generation time only), and button click approvals/rejections complete in < 2 seconds.
