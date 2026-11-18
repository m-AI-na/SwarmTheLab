# Swarm in the Lab: Building a Fleet of Biomedical AI Agents

Materials for the hands-on workshop at BiocAsia 2026, a satellite of ABACBS 2026 at RMIT University.

Over one afternoon you'll run three multi-agent systems on one question: **is B7-H3 (CD276) a credible therapeutic target in lung cancer, and which kind of drug suits it?** Each module passes its results to the next.

| When | Notebook | What you do |
|---|---|---|
| 13:00 | `00_preflight.ipynb` | Check that your VM is ready, and enter your workshop keys (Step 0) |
| 13:35–14:25 | `01_virtual_lab.ipynb` | **Module 1 · Virtual Lab:** a team of AI scientists turns the question into a research strategy |
| 14:40–15:30 | `02_paper2agent.ipynb` | **Module 2 · Paper2Agent:** published papers become tools that Claude Code calls to run the strategy's analyses |
| 15:40–16:30 | `03_virtual_biotech.ipynb` | **Module 3 · Virtual Biotech:** a company of agents gathers the evidence and writes a report |

Module 4, checking the agents' work, starts from the summary that Module 3 saves.

## On the day

Your workshop VM already has everything installed: VS Code, the workshop environment, the data and these notebooks.

1. **Log in** to your VM with the DCV link, username and password the facilitators give you.
2. **Open VS Code** (click **Activities** at the top left and type `Code`). Choose **File → Open Folder…** and open the `workshop` folder in your home folder. If VS Code asks whether you trust the authors of the files, choose **Yes, I trust the authors**.
3. **Run the self-test.** Open `00_preflight.ipynb`. If VS Code asks for a kernel, choose **Workshop (Python 3.12)**. Run Step 0, then the rest of the notebook. If anything fails, show the `PREFLIGHT` line at the end to a helper.
4. **Sign in to Claude Code** when Module 1 asks: click the Claude icon in VS Code's left sidebar and sign in with your own Claude account. Claude Code needs a paid plan (Pro or above).
5. **At the end,** type `/logout` in the Claude Code panel, and sign out of anything else you signed in to.

### Keeping keys safe

- Enter workshop API keys only in the hidden box in Step 0 of the self-test. The facilitators will say if your VM already has them. Never paste a key into a notebook cell, a terminal or a chat with Claude.
- The notebooks show a key only as a short fingerprint, and hide anything key-shaped in their output.
- The workshop keys have spending caps and are cancelled after the workshop.

## What's in this repository

```
├── 00_preflight.ipynb         self-test, and Step 0 for your keys
├── 01_virtual_lab.ipynb       Module 1 · Virtual Lab
├── 02_paper2agent.ipynb       Module 2 · Paper2Agent
├── 03_virtual_biotech.ipynb   Module 3 · Virtual Biotech
├── environment.yml            the workshop conda environment (Python 3.12)
├── claude_settings.json       Claude Code permissions used on the workshop VMs
├── LICENSE
└── tools
    ├── make_workshop_dataset.py  builds Module 2's lung adenocarcinoma dataset from CELLxGENE Census
    ├── prepare_image.py       removes keys and sign-ins before the VM is copied for participants
    ├── setup_paper_agents.py  installs the Scanpy and TISSUE paper agents for Module 2
    └── vbt_workshop.py        runs the Virtual Biotech with a small team (Module 3 calls it)
```

The datasets, the Paper2Agent and Virtual Biotech code, the Open Targets data and the pre-run results aren't in this repository. The facilitators set them up on the workshop VM.

## Running it after the workshop

To run the notebooks on your own Ubuntu machine (22.04 or later), ask the facilitators for their setup notes. You'll need your own OpenAI and Anthropic API keys, a Claude account with Claude Code, and about 100 GB of disk space, mostly for Module 3's copy of Open Targets.

## Built on

- [Virtual Lab](https://github.com/zou-group/virtual-lab) (Module 1)
- [Paper2Agent](https://github.com/jmiao24/Paper2Agent) (Module 2)
- [The Virtual Biotech](https://github.com/harrisongzhang/TheVirtualBiotech) (Module 3)

Each has its own licence. The workshop materials in this repository are covered by `LICENSE`.
