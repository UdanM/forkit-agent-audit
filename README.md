# Forkit AI AutoDiscover

Forkit AutoDiscover is a local-first dashboard for finding AI tools, giving them stable IDs, and reviewing what they did. It scans supported AI client settings and Model Context Protocol (MCP) configurations, then connects tool identities to recorded activity when the evidence supports a match.

[Quick start](#quick-start) · [Demo walkthrough](#demo-walkthrough) · [PC and VM testing](#pc-and-vm-testing) · [Run the tests](#run-the-tests)

[![CI](https://github.com/UdanM/forkit-agent-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/UdanM/forkit-agent-audit/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

## Quick start

Install Python 3.10 or newer and Git, then clone the project.

```sh
git clone https://github.com/UdanM/forkit-agent-audit.git
cd forkit-agent-audit
```

On **Windows**, double-click `Start AutoDiscover.cmd` in File Explorer or run it from PowerShell.

```powershell
& '.\Start AutoDiscover.cmd'
```

On **macOS or Linux**, run `sh ./start-autodiscover.sh`. You can also start the app directly from the repository folder on any platform.

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 127.0.0.1
```

Use `python3` if that is your Python command. Keep the terminal running and open the **Local URL** Streamlit prints, usually [http://127.0.0.1:8501](http://127.0.0.1:8501).

## Demo walkthrough

Leave the sidebar set to **Demo with sample data** and click **Set up the demo in one click**. This loads three sample tools from two settings files and issues a passport for each.

1. In **Passports**, compare the two tools named `postgres-mcp`. Their separate configuration sources give them distinct IDs. Click **Simulate app restart** in the sidebar to see the same IDs recognized on a fresh scan.
2. Turn on **Simulate a settings edit** and open **Changes**. The edit to `filesystem-mcp` is flagged for review while its passport ID stays the same.
3. In **Event review**, select `evt-0002` and check its source and timestamp. Give consent and click **Link action to tool** to record the write and its missing approval. Try `evt-0004` for an event that remains unlinked because its source is ambiguous.
4. Open **Map** to follow the path from the settings file to the passport, linked action, and incident. In **Activity timeline**, inspect the recorded actions, agent delegation, and simulated human edits. In **Ask Forkit**, try “Who was the human in the workflow?”
5. Use **Download audit report (ZIP)** in the sidebar to export the demo evidence. Expand **Audit records** to save and verify a separate Git archive.

## PC and VM testing

Open **Testing** and expand **Review a previous run** to see the comparison using the bundled sample. Both scripted agents receive the same report prompt. The VM receives an extra attachment that asks it to write a harmless file outside the task. Forkit flags the extra write as **Possible indirect prompt injection** and shows the observer record and timestamp.

To run the comparison live, start the VM runner, forward its loopback port over SSH, and configure the local dashboard with the port and a private token file. Then type one prompt and click **Send to both agents**. The dashboard shows each report and sends the staged attachment only to the VM. The [VM setup guide](demo/agents/README.md#prompt-injection-comparison) covers the runner command and connection settings.

## Scan your own computer

Choose **Scan this computer** to read supported client settings and detect installed AI tools on the machine running the dashboard. The scan is read-only and uses a separate registry from the demo. Forkit redacts configuration secrets, leaves unsupported claims as `UNKNOWN`, and requires explicit consent before linking an event. See the [supported clients and settings paths](AUTODISCOVER_README.md#real-local-machine-mode).

## Run the tests

Run these commands from the repository root after installing the demo requirements.

```sh
python -m pytest -q tests/test_acceptance.py
python -m pytest -q tests/test_prompt_pair.py tests/test_prompt_pair_live.py
```

The acceptance tests cover restart recognition, distinct IDs for same-name tools, consented linking, missing approvals, and incident recording. The Testing suite exercises the attachment comparison with local test processes. For the full repository suite, install the development extras first.

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Project guides

| Guide | What it covers |
| --- | --- |
| [AutoDiscover technical guide](AUTODISCOVER_README.md) | Discovery, identity rules, event linking, audit exports, and the activity evidence model |
| [Demo and pitch guide](docs/PITCH_PLAN.md) | A walkthrough for presenting the dashboard |
| [Agent scenarios](demo/agents/README.md) | Scripted workflows, runtime evidence capture, and the PC-to-VM test |
| [Forkit Core reference](FORKIT_CORE_README.md) | The underlying passport SDK, CLI, registry, and framework integrations |

This project builds on [Forkit Core](https://github.com/Forkit-Dev-Core/Forkit_Dev) and the [merged AutoDiscover hackathon project](https://github.com/eren-mutlu/forkit-autodiscover-hackathon). See [LICENSE](LICENSE) and [NOTICE](NOTICE).
