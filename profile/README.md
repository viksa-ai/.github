# Viksa AI

> Build, deploy, observe, and govern teams of AI agents on a single platform — without re-implementing infrastructure for every team.

Welcome to the official GitHub organization for **Viksa AI**. We are building **Flagship**, an opinionated, enterprise-grade, multi-tenant agent platform designed to run autonomous AI workforces with full durability, observability, and governance.

---

## 🚀 The Flagship Platform

Flagship provides the complete infrastructure substrate for AI agents. Instead of re-building logging, cost tracking, tool execution, and human-in-the-loop approvals for every agent, Flagship provides a unified, production-hardened platform.

### Key Capabilities

*   **⚡ Durable Execution**: Backed by Temporal workflows, ensuring agent execution is fully resilient to pod restarts, network drops, and long-running step pauses.
*   **🔍 High-Fidelity Observability**: Complete waterfall timeline of agent executions, including detailed logs of thinking steps, LLM calls, tool execution, and costs.
*   **🤝 Human-in-the-Loop (HITL)**: Policy-driven manual approvals for destructive actions (e.g., database writes, external API calls) integrated directly via Email and Slack.
*   **⚖️ Cost & Token Telemetry**: Precise token tracking and real-time cost rollups per execution, agent, and workforce.
*   **🛡️ Enterprise Governance**: Production-protected agents requiring dual-operator approvals, comprehensive audit logs, and secure secrets vaulting.
*   **🎯 Immutable Prompt Versioning**: Automated prompt versioning with built-in A/B testing and routing capabilities.
*   **🧪 Automated Eval Harness**: Integrated evaluation runner supporting exact match, regex, and LLM-as-a-judge patterns.

---

## 🏗️ Architecture & Repositories

Flagship is architected as a highly decoupled fleet of **FastAPI microservices** behind an **Nginx ingress gateway**, powered by **Next.js (App Router)** on the frontend, and **MongoDB, Redis, and Temporal** as the durable data substrate.

### Core Repositories

Our codebase is separated into independent, highly focused repositories to ensure clean boundaries, security, and independent CI/CD:

*   **[ui-service](https://github.com/viksa-ai/ui-service)**: The Next.js operator console for building agents, managing workforces, monitoring runs, and live debugging.
*   **[auth-service](https://github.com/viksa-ai/auth-service)**: Identity, RBAC, JWT token issuance, secrets vault, and audit log writer.
*   **[builder-service](https://github.com/viksa-ai/builder-service)**: Agent/workforce authoring, prompt versioning, eval runner, and hosted MCP registry.
*   **[chat-service](https://github.com/viksa-ai/chat-service)**: Run orchestrator, agent execution loop, run-event log, and live debugger controller.
*   **[pulse-service](https://github.com/viksa-ai/pulse-service)**: Temporal workflow launcher and execution plumbing.
*   **[hosted-images-service](https://github.com/viksa-ai/hosted-images-service)**: Docker images and Temporal workers (`chrona-worker-cloud` and `chrona-worker-secure`) for secure agent code execution.
*   **[volt-service](https://github.com/viksa-ai/volt-service)**: Volt Slack integration, Slack bot, and interactive approval components.
*   **[volt-engine-service](https://github.com/viksa-ai/volt-engine-service)**: Core Volt execution engine and high-performance Redis prompt cache.
*   **[marketplace-service](https://github.com/viksa-ai/marketplace-service)**: Public and private agent/workforce listings, installations, and billing provider integrations.
*   **[workflow-service](https://github.com/viksa-ai/workflow-service)**: Multi-agent workflow orchestration and state tracking.
*   **[scheduler-service](https://github.com/viksa-ai/scheduler-service)**: Cron-style job scheduler and trigger fan-out.
*   **[worker-service](https://github.com/viksa-ai/worker-service)**: Background state reconciliation and long-running pipelines.
*   **[devspace-service](https://github.com/viksa-ai/devspace-service)**: Ephemeral, in-cluster developer environments for editing and testing agents.
*   **[devops](https://github.com/viksa-ai/devops)**: Global Kubernetes manifests, Temporal Helm charts, shared libraries (`platform-traces`, `platform-metrics`), and deployment scripts.

---

## 🛠️ Technology Stack

*   **Frontend**: Next.js 14+, React, TypeScript, Tailwind CSS, Shadcn UI
*   **Backend**: Python 3.11+, FastAPI, Slack Bolt SDK
*   **Workflows & Queues**: Temporal, Redis (Pub/Sub & Streams)
*   **Databases**: MongoDB (multi-tenant, per-account database partitioning)
*   **Infrastructure**: Kubernetes (GKE), Docker, Nginx Ingress, Let's Encrypt (cert-manager)
*   **CI/CD**: GitHub Actions, Docker Buildx, GitHub Container Registry (GHCR)

---

## 🔒 Security & Compliance

Viksa AI is designed from the ground up for secure enterprise deployment:
*   **Data Isolation**: Strict multi-tenant data partitioning with separate databases per account.
*   **Sandboxed Execution**: Secure agent code execution inside isolated, restricted container runtimes.
*   **Auditability**: Every single state mutation or administrative action is recorded in an append-only audit trail.
*   **No Reverts Policy**: Strict schema evolution and additive database migrations to prevent data loss or downtime.

---

<p align="center">
  Proprietary. © 2026 Viksa AI. All rights reserved.
</p>
