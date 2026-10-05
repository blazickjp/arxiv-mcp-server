"""Real paper fixtures for regression testing outline parsing (issue #284).

These are trimmed excerpts from real arXiv HTML→markdown papers, capturing
the specific heading patterns that main and early revisions mishandled. Each
excerpt notes the arXiv ID and approximate source line range.
"""

# KAN 2404.19756 (lines ~85-150): Abstract-first, split '2'/'2.2'/'3'/'4'
# Tests split-number headings without trailing periods
KAN_EXCERPT = """Abstract

KAN: Kolmogorov-Arnold Networks.

1
Introduction

The dominant approach to neural networks.

2
Kolmogorov–Arnold Networks (KAN)

Multi-Layer Perceptrons are inspired by the universal approximation theorem.

2.1
Kolmogorov-Arnold Representation theorem

Vladimir Arnold and Andrey Kolmogorov established that if f is continuous.

2.2
KAN architecture

Suppose we have a supervised learning task.

3
KANs are accurate

In this section, we demonstrate that KANs are more effective.

4
KANs are interpretable

In this section, we show that KANs are interpretable.
"""

# Switch 2101.03961 (lines ~70-150, ~1140-1210): Table rows 12/16/64/32
# Must reject table numbers that appear after section 2
SWITCH_EXCERPT = """1
Introduction

Switch Transformers scale the model parameters.

2
Switch Transformer

The guiding design principle for Switch Transformers.

2.1
Simplifying Sparse Routing

Mixture of Expert Routing proposed by Shazeer et al.

12
T5-Large

16
T5-XXL

64
Switch-Base

32
Model

3
Scaling Properties

Analysis of scaling behavior.
"""

# LoRA 2106.09685 (lines ~560-570): '5.2 RoBERTa base/large' is a REAL heading
# This was mislabeled in round 3; the heading is real, followed by Table 2
LORA_EXCERPT = """1
Introduction

Low-Rank Adaptation of Large Language Models.

2
Problem Statement

We define the problem.

3
Aren't Existing Solutions Good Enough?

Existing approaches have limitations.

4
Our Method

We propose LoRA.

5
Empirical Experiments

We evaluate the downstream task performance of LoRA.

5.1
Baselines

We compare with several baselines.

5.2
RoBERTa base/large

Table data follows.

6
Related Works

Prior work on parameter-efficient fine-tuning.
"""

# CoT 2201.11903 (synthetic): 60/80/90 dataset rows must not become sections
COT_EXCERPT = """1
Introduction

Chain-of-thought prompting.

60
GSM8K

80
Model scale

2
Chain-of-Thought Prompting

Explanation of the method.

3
Arithmetic Reasoning

First real section.
"""

# DAOP 2501.10375 (lines ~260-280, ~470-510): Guards for %, =, Phi-3.5, Ours
# Tests content guards that reject table data and pseudocode
DAOP_EXCERPT_GUARDS = """V
Experimental Evaluation

We evaluate DAOP against several baselines.

V-A
Experimental Setup

Our setup uses Mixtral 8x7B and Phi-3.5 MoE.

SwapNum = 0.5

HotExps = getTopKActiveExperts(ExpsCPU, SwapNum);

ColdExps = getBottomKActiveExperts(ExpsGPU, SwapNum);

V-B
Speedup

DAOP outperforms Fiddler by 40.4%.

Impro. (%)

Mixtral 8x7B

Phi-3.5 MoE

Fig 10 compares performance.

V-C
Energy Efficiency

Table IV compares energy efficiency.

Ours

14.37

27.07

V-D
Accuracy Results

Table V presents model accuracy.

VI
Discussion

DAOP provides significant improvements.
"""

# Llama 2 2307.09288 (synthetic): Multiple subsections must be kept
LLAMA2_EXCERPT = """1
Introduction

Open foundation and fine-tuned chat models.

2
Pretraining

Details of pretraining approach.

2.1
Pretraining Data

Data sources and preprocessing.

2.2
Training Details

Training hyperparameters.

2.2.1
Training Hardware & Carbon Footprint

Carbon footprint details.

3
Fine-tuning

RLHF procedure.

3.1
Supervised Fine-Tuning (SFT)

SFT details.
"""

# Attention 1706.03762 (synthetic): "3 Model Architecture" must be kept
ATTENTION_EXCERPT = """1
Introduction

The dominant sequence transduction models.

2
Background

Prior attention mechanisms.

3
Model Architecture

The Transformer model architecture.

3.1
Encoder and Decoder Stacks

Architecture details.
"""

# Mistral 2310.06825 (synthetic): "2 Architectural details" must be kept
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

# DeepSeek-R1 2501.12948 (synthetic): "2 DeepSeek-R1-Zero" must be kept
DEEPSEEK_R1_EXCERPT = """Abstract

We introduce DeepSeek-R1.

1
Introduction

Large language models show strong reasoning.

2
DeepSeek-R1-Zero

Pure reinforcement learning approach.

2.1
Group Relative Policy Optimization

GRPO training details.

3
DeepSeek-R1

Distillation from R1-Zero.
"""

# Mamba 2312.00752 (synthetic): Subsections must be kept
MAMBA_EXCERPT = """1
Introduction

Sequence models with selective state spaces.

2
State Space Models

Background on SSMs.

2.1
Discretization

Converting continuous to discrete.

3
Selective State Space Models

Selection mechanism.

3.1
Motivation: Selection as a Means of Compression

Why selection matters.
"""

# DAOP 2501.10375 (synthetic for legacy test compatibility)
DAOP_EXCERPT = """Abstract

DAOP: Dynamic Allocation of MoE Parameters.

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
