# WorkerGPT Orchestrator

WorkerGPT coordinates bounded ChatGPT research agents with durable local state. It supports the original Researcher → Critic → Synthesizer pipeline and a section-by-section scientific dossier workflow.

Quick start:

```bash
python -m orchestrator create kintsugi --name "Kintsugi research"
python -m orchestrator workflow init kintsugi --vault /path/to/vault
python -m orchestrator workflow status kintsugi
python -m orchestrator --real workflow run kintsugi --topic KSR-10
```

The durable workflow never replaces a topic file until six independently reviewed sections pass a strict final audit and local validation. See [docs/RESEARCH_WORKFLOW.md](docs/RESEARCH_WORKFLOW.md) and [docs/USAGE.md](docs/USAGE.md).
