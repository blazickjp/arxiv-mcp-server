"""Real paper fixtures for regression testing outline parsing (issue #284).

These are trimmed excerpts from real arXiv HTML→markdown papers,
capturing the specific heading patterns that main mishandles.
"""

# Switch Transformer (2101.03961): table numbers 12/16/64 must not become sections
SWITCH_EXCERPT = """1
Introduction

Switch Transformers scale the model parameters.

12
T5-Large

16
T5-XL

64
Switch-C

32
Model

2
Switch Transformer

We introduce the Switch Transformer architecture.

3
Scaling Properties

Analysis of scaling behavior.
"""

# LoRA (2106.09685): 5.2 table row must not become section
LORA_EXCERPT = """1
Introduction

Low-Rank Adaptation of Large Language Models.

5.2
RoBERTa base/large

2
Problem Statement

We propose LoRA.

3
Aren't Existing Solutions Good Enough

Prior work on adaptation.
"""

# CoT (2201.11903): 60/80 dataset rows must not become sections
COT_EXCERPT = """1
Introduction

Chain-of-thought prompting.

60
GSM8K

80
Model scale

2
Arithmetic Reasoning

First real section.

3
Commonsense Reasoning

Second real section.
"""

# Attention is All You Need (1706.03762): "3 Model Architecture" must be kept
ATTENTION_EXCERPT = """1
Introduction

The dominant sequence transduction models.

2
Background

Prior attention mechanisms.

3
Model Architecture

The Transformer model.

3.1
Encoder and Decoder Stacks

Architecture details.
"""

# Mistral (2310.06825): "2 Architectural details" must be kept
MISTRAL_EXCERPT = """1
Introduction

Mistral 7B.

2
Architectural details

Mistral uses Grouped-Query Attention.

2.1
Sliding Window Attention

SWA details.

3
Results

Evaluation results.
"""

# DeepSeek-R1 (2501.12948): "2 DeepSeek-R1-Zero" must be kept
DEEPSEEK_R1_EXCERPT = """Abstract

We introduce DeepSeek-R1.

1
Introduction

Large language models show strong reasoning.

2
DeepSeek-R1-Zero

Pure reinforcement learning approach.

2.1
Training Setup

RL setup details.

3
DeepSeek-R1

Distillation from R1-Zero.
"""

# Llama 2 (2307.09288): many subsections must be kept
LLAMA2_EXCERPT = """1
Introduction

Open foundation and fine-tuned chat models.

2
Pretraining

Details of pretraining approach.

2.1
Pretraining Data

Data sources.

2.2
Training Details

Training hyperparameters.

3
Fine-tuning

RLHF procedure.

3.1
Supervised Fine-Tuning

SFT details.

3.2
Reinforcement Learning with Human Feedback

RLHF details.

3.2.1
Reward Modeling

Reward model training.
"""

# Mamba (2312.00752): subsections must be kept
MAMBA_EXCERPT = """1
Introduction

Sequence models with selective state spaces.

2
State Space Models

Background on SSMs.

2.1
Discretization

Converting continuous to discrete.

2.2
Computation

Efficient computation.

3
Selective State Space Models

Selection mechanism.

3.1
Motivation: Selection

Why selection matters.

3.2
Improving SSMs with Selection

Architecture changes.
"""

# DAOP (2501.10375): 21 sections, no table fakes with % or =
DAOP_EXCERPT = """Abstract

Detecting Anomalous Operations in Programs.

1
Introduction

Anomaly detection approach.

1
Mixtral 8x7B

1
Avg. Accuracy: 84.11%

1
SwapNum = 0.5

2
Related Work

Prior work on anomaly detection.

3
Methodology

Our approach.

4
Experiments

Setup and evaluation.

5
Results

Evaluation results.
"""
