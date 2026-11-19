# Sentinel Twin on real fraud data

The app's GAT is trained on the synthetic twin, so these experiments check the same NumPy GAT on three public fraud graphs, then test new accounts and hybrid models. Every number below comes from the scripts in `scripts/`; the datasets are not committed. See the [README](../README.md#results-at-a-glance) for the summary.

Contents: [Amazon](#first-real-dataset-the-amazon-fraud-graph) · [Elliptic](#second-real-dataset-elliptic-bitcoin-transactions) · [T-Finance](#third-real-dataset-t-finance-a-financial-transaction-network) · [New accounts](#new-accounts-does-the-graph-rescue-accounts-with-no-history) · [Hybrid](#hybrid-give-the-random-forest-the-graph)

## First real dataset: the Amazon fraud graph

To check the GAT outside our own synthetic twin, `scripts/real_data_eval.py` runs it on the public **Amazon fraud dataset** used by CARE-GNN (Dou et al., CIKM 2020): 8,639 labelled Amazon users (9.5% fraudulent), 25 handcrafted review features, and three real relations: **U-P-U** (reviewed the same product), **U-S-U** (gave the same star rating within a week) and **U-V-U** (top 5% mutual review-text similarity). For each of 3 seeds it samples 2,500 users (stratified), splits 60/20/20 and compares a class-weighted logistic regression with the same NumPy GAT the app ships, given no edges, each relation, or all of them (200 epochs, lr 0.01).

| Test set (501 users, mean ± std over 3 seeds) | AUC | Recall, top 5% reviewed | Precision, top 5% | Recall, top 10% | Precision, top 10% |
|---|---|---|---|---|---|
| Logistic regression (features only) | **0.962 ± 0.012** | 0.521 | 0.962 | **0.826** | **0.778** |
| GAT, no edges | 0.959 ± 0.015 | 0.521 | 0.962 | 0.819 | 0.771 |
| GAT on U-P-U | 0.868 ± 0.009 | 0.451 | 0.833 | 0.576 | 0.542 |
| GAT on U-S-U | 0.910 ± 0.012 | 0.424 | 0.782 | 0.715 | 0.673 |
| GAT on U-V-U | 0.877 ± 0.042 | 0.403 | 0.744 | 0.646 | 0.608 |
| GAT on all three | 0.907 ± 0.032 | 0.424 | 0.782 | 0.694 | 0.654 |
| **GAT on all three, + skip connection** | **0.968 ± 0.013** | 0.507 | 0.936 | 0.799 | 0.752 |
| GAT on all three, + skip + neighbour filter | 0.956 ± 0.021 | 0.514 | 0.949 | 0.812 | 0.765 |

**What this honestly shows: on this real graph, the edges hurt.** The features alone are strong (0.96 AUC, and reviewing the riskiest 10% of users catches about 83% of fraud), and adding any relation drops the GAT to 0.87-0.91. The reason is camouflage, the effect CARE-GNN was built to handle: 95% of edges join users with the same label overall, but that is driven by the honest majority. Seen from a fraudster, only 11-21% of their neighbours are also fraudsters (base rate 9.5%), so a plain GAT that averages over neighbours mixes a fraudster's signal with mostly honest ones.

What it means for Sentinel Twin:
* The GAT's value is specific to **fraud that shares infrastructure** (rings and device farms reusing devices and IPs), which is what the twin's graph links. "Reviewed the same product" is not that kind of link. This matches our synthetic result: no-graph models match the GAT on account-level AUC, and the graph helps when a new account joins a known ring.
* **Follow-up: a skip connection fixes most of this.** The last two rows were run later with the same seeds, samples and epochs, using two opt-in GAT options (both off by default, so the app is unchanged). *Skip* adds each node's own transformed features to what it gathers from its neighbours (`A @ Z + Z`). *Neighbour filter* (CARE-GNN style) keeps, for each user, the half of their neighbours whose score from a logistic regression fitted on training users only is closest to their own. With skip, edges go from costing about 0.05 AUC to adding about 0.01 (0.968 vs 0.959 without edges), the best AUC of any model here. That gain is inside one standard deviation, and at a 5% or 10% review budget skip is still just below logistic regression. The filter does not help on top of skip (0.956).
* Still missing from CARE-GNN: learning how much to trust each relation.
* The dataset is not committed. To reproduce: download `Amazon.zip` from the CARE-GNN repo, unzip it, then `python scripts/real_data_eval.py path/to/Amazon.mat --n 2500 --seeds 3 --epochs 200` (about an hour on a laptop CPU with all eight models, since the GAT uses a dense mask).

## Second real dataset: Elliptic Bitcoin transactions

`scripts/elliptic_eval.py` runs the same GAT on the **Elliptic** dataset (Weber et al., 2019): 203,769 Bitcoin transactions linked by 234,355 payment flows over 49 time steps, 46,564 labelled (9.8% illicit). Protocol as in the paper: train on time steps 1-34, test on 35-49 (16,670 labelled transactions, 1,083 illicit). Elliptic ships 72 *aggregated* features that already summarise each transaction's neighbours, so to measure what the graph itself adds, **every model gets only the 93 local features** (the paper's 94 minus the time step). The 165-feature models are a reference line only. The GAT trains on one graph per time step: the labelled transactions plus their 1-hop neighbours, capped at 2,500 nodes (16 of 49 steps hit the cap). It runs 80 epochs, with one seed.

Unlike Amazon, these edges carry signal: **51% of an illicit transaction's labelled neighbours are also illicit**, against a 9.8% base rate (98% for licit transactions).

| Illicit class, test steps 35-49 | F1 | Precision | Recall | AUC | Recall, top 5% reviewed | Precision, top 5% |
|---|---|---|---|---|---|---|
| Logistic regression, local | 0.242 | 0.141 | 0.860 | 0.866 | 0.133 | 0.173 |
| **Random forest, local** | **0.772** | **0.844** | 0.712 | 0.893 | **0.696** | **0.904** |
| GAT, no edges, local | 0.377 | 0.247 | 0.800 | **0.894** | 0.646 | 0.839 |
| GAT + transaction edges, local | 0.527 | 0.431 | 0.680 | 0.873 | 0.531 | 0.689 |
| GAT + edges, + skip connection | 0.507 | 0.379 | 0.765 | **0.898** | 0.553 | 0.718 |
| GAT + edges, + skip + neighbour filter | 0.471 | 0.337 | 0.781 | **0.898** | 0.281 | 0.365 |
| *Reference:* random forest, all 165 (includes neighbour aggregates) | 0.802 | 0.900 | 0.724 | 0.918 | 0.721 | 0.936 |

F1, precision and recall are at a 0.5 cutoff. Logistic regression and the GAT use balanced class weights, which pushes them towards recall at that cutoff. The random forest is unweighted, as in the paper. The two top-5% columns don't depend on a cutoff.

What this honestly shows:
* **Edges help the GAT's precision at the cutoff but not its ranking.** Adding edges lifts F1 from 0.38 to 0.53 (precision 0.25 to 0.43), but AUC falls from 0.894 to 0.873, and the share of fraud caught in the riskiest 5% falls from 65% to 53%. So the graph changes how confident the model is more than how well it ranks.
* **A random forest on the same local features is still clearly best** (F1 0.77, 90% precision in the riskiest 5%). This matches the paper, where a random forest beat a graph convolutional network. Tree models fit these nonlinear features much better than a linear model (0.24) or our small GAT.
* **Follow-up with the skip connection and neighbour filter** (see the Amazon section; same seed and epochs). Skip gives the GAT the best AUC on local features (0.898, against 0.894 without edges and 0.893 for the random forest) and recovers some of the top-5% recall that plain edges lost (55% vs 53%, still below 65% without edges). F1 at 0.5 stays about where plain edges left it (0.51). **The filter does not help here:** AUC is the same, but only 28% of fraud is caught in the riskiest 5%, so its top-ranked transactions are worse. Its one gain is after the shutdown at step 43 (F1 0.12, the best of any model there), which is too small to count. The random forest remains clearly the best practical model.
* **Every model fails after the dark-market shutdown at time step 43:** illicit F1 is at most 0.12 from step 43 on, for all models (it was 0.34-0.90 before). That is the same failure the paper reports.
* Caveats: one seed, a small GAT (8 hidden units x 4 heads, 80 epochs) and capped step graphs.

Across both datasets the lesson is the same. The graph only pays off when links connect fraud to fraud: shared devices, IPs or cards rather than shared products. A skip connection to the node's own features is the CARE-GNN-style part that helps most; neighbour filtering helps less consistently. To reproduce, download the three CSVs from `https://data.pyg.org/datasets/elliptic/`, unzip them into a folder, and run `python scripts/elliptic_eval.py that/folder --epochs 80` (about 50 min with all four GAT variants, needs `scikit-learn` and `pandas`).

## Third real dataset: T-Finance (a financial transaction network)

`scripts/tfinance_eval.py` runs the same comparison on **T-Finance** (Tang et al., ICML 2022, also part of the GADBench benchmark): 39,357 real accounts with 10 account features, linked by 21.2M transaction records (about 1,078 links per account). 4.6% of the accounts are labelled anomalous: fraud, money laundering or online gambling. This is the closest public dataset to a payments business. The file is a DGL graph, and DGL has no Mac build for this Python, so the script reads the arrays straight from the file. The protocol matches Amazon: 5 seeds, a stratified sample of 2,500 accounts, the induced subgraph (about 66 links per account remain), 60/20/20 split and 200 GAT epochs. Features are log-transformed and then standardised.

The links carry strong signal: **54% of an anomalous account's neighbours are anomalous, against 2.4% for a normal account** (base rate 4.6%).

| Test set (500 accounts, about 23 anomalous; mean ± std over 5 seeds) | AUC | F1 at 0.5 | Recall, top 5% reviewed | Precision, top 5% |
|---|---|---|---|---|
| Logistic regression (features) | **0.956 ± 0.009** | 0.458 | 0.748 | 0.688 |
| Random forest (features) | 0.949 ± 0.018 | **0.746** | 0.730 | 0.672 |
| GAT, no edges | 0.917 ± 0.018 | 0.413 | 0.713 | 0.656 |
| GAT + transaction edges | 0.944 ± 0.037 | 0.533 | 0.757 | 0.696 |
| **GAT + edges, + skip connection** | **0.958 ± 0.031** | 0.637 | 0.791 | 0.728 |
| **GAT + edges, + skip + neighbour filter** | 0.946 ± 0.039 | 0.641 | **0.826** | **0.760** |

What this honestly shows:
* **Here the graph helps the GAT on every metric:** AUC goes from 0.917 to 0.944, F1 from 0.41 to 0.53, and the share of fraud caught in the riskiest 5% from 71% to 76%. It is the first of the three datasets where that happens, and the one where the links connect fraud to fraud most strongly.
* **With edges, the GAT catches up with the feature-only models but does not clearly beat them.** It is level at a 5% review budget (76% of fraud caught vs 75% for logistic regression and 73% for the random forest) and level on AUC. The differences are inside the noise: each test split has only about 23 anomalous accounts, and the standard deviations are 0.04-0.10.
* Without edges our GAT is weaker than logistic regression on the same features (0.917 vs 0.956 AUC), because it has no skip connection to a node's own features. The edges partly make up for that.
* **Follow-up with the skip connection and neighbour filter** (last two rows, same seeds and samples; see the Amazon section). Skip lifts the edge GAT to AUC 0.958 (level with logistic regression at 0.956) and F1 0.64. Adding the filter gives the best review-budget result of any model: 82.6% of fraud caught in the riskiest 5%, against 74.8% for logistic regression. That is 19 of 23 anomalous test accounts on every seed (its std is 0.000 because the metric is coarse), against about 17 for logistic regression. The random forest still has the best F1 at a 0.5 cutoff (0.75), and with test splits this small the gaps remain suggestive rather than conclusive.

**Across the three datasets:** the graph's value tracks how strongly links connect fraud to fraud. On Amazon (11-21% of a fraudster's neighbours are fraudsters) the edges hurt. On Elliptic (51%) they shift the GAT's confidence but not its ranking. On T-Finance (54%, against 2.4% for normal accounts) they help it on every metric. **With the skip connection** the edge GAT is at or above every feature-only model on AUC on all three datasets (Amazon 0.968, Elliptic 0.898, T-Finance 0.958). The neighbour filter helped only on T-Finance's top-5% recall. On Amazon and Elliptic it did as well as skip alone or worse, so it is kept as an option rather than a default. Sentinel Twin's graph links accounts through shared devices and IPs, the strongly linked kind. To reproduce, download `tfinance.zip` from the BWGNN repo's Google Drive folder (github.com/squareRoot3/Rethinking-Anomaly-Detection), unzip it, then run `python scripts/tfinance_eval.py path/to/tfinance --seeds 5` (about 70 min with all four GAT variants).

## New accounts: does the graph rescue accounts with no history?

The public datasets score established accounts with full feature histories, which is where feature-only models are strongest. Sentinel Twin's real case is a **new** account: almost no history, but it may share a device, IP or counterparty with known fraud. `scripts/cold_start_eval.py` simulates that on T-Finance and Amazon. It uses the same seeds 0-2 and samples as above, and every model is trained on full-feature training accounts. Only the **test** accounts are made new, in one of two ways:
* **few features:** they keep only the ~20% least informative features (2 of 10 on T-Finance, 5 of 25 on Amazon, ranked on the training split), and the rest are set to the training mean;
* **no features:** every feature is set to the training mean, so every new account looks the same on its own.

Their links are kept. That is the best case for the graph, since a genuinely new account usually also has fewer links.

| T-Finance, new test accounts (3 seeds) | AUC, full features | AUC, few | AUC, none | Fraud caught in riskiest 5%, none |
|---|---|---|---|---|
| Logistic regression | 0.953 | 0.364 | 0.500 | none |
| Random forest | 0.943 | 0.416 | 0.500 | none |
| GAT, no edges | 0.903 | 0.390 | (constant) | |
| **GAT + edges, skip** | 0.953 | 0.911 | 0.935 ± 0.027 | 72.5% |
| **Random forest + neighbour features** (see Hybrid below) | 0.980 | **0.928** | **0.955 ± 0.011** | 72.5% |
| *Control:* GAT + **randomly rewired** edges | | | 0.456 ± 0.074 | 0% |
| *Control:* random forest + neighbour features, rewired edges | | | 0.623 ± 0.041 | 11.6% |

| Amazon, new test accounts (3 seeds) | AUC, full features | AUC, few | AUC, none | Fraud caught in riskiest 5%, none |
|---|---|---|---|---|
| Logistic regression | 0.960 | 0.524 | 0.500 | none |
| Random forest | 0.974 | 0.565 | 0.500 | none |
| GAT, no edges | 0.959 | 0.557 | (constant) | |
| GAT + edges, skip | 0.968 | 0.691 | 0.690 ± 0.009 | 20.1% |
| **Random forest + neighbour features** | 0.953 | **0.836** | **0.819 ± 0.024** | 25.0% |
| *Control:* GAT + randomly rewired edges | | | 0.540 ± 0.039 | 17.4% |
| *Control:* random forest + neighbour features, rewired edges | | | 0.708 ± 0.044 | 16.0% |

"None" means the feature-only models give every new account the same score, so they cannot rank them at all (AUC 0.5). Their "fraud caught" column reflects only how tied scores happen to be ordered; with no ranking at all, the fair figure is about 5%. The full-feature GAT numbers come from the runs above (seeds 0-2). The neighbour features are described under Hybrid; their known-fraud share uses training labels only.

What this honestly shows:
* **Without history, feature-only models are blind or worse.** With a few weak features left they fall *below* 0.5 on T-Finance (0.36-0.42): they learned what a full account looks like, and a half-blank one misleads them.
* **The links rescue new accounts.** On T-Finance, with no features at all, the GAT still reaches AUC 0.935 and catches 72.5% of fraud in the riskiest 5% (about the same share as with full features). On Amazon, despite the camouflage, it goes from 0.5 to 0.69.
* **The control confirms this is the real links, not a leak.** The control rewires every account's links at random, keeping roughly its number of links (about 17% of T-Finance edges are lost as duplicates when rewiring a graph this dense). On the rewired graph the GAT drops to chance (0.46 T-Finance, 0.54 Amazon). The random forest keeps a little signal (0.62 / 0.71), because its neighbour features include the account's link count, and rewiring keeps that. The code was also checked: the test accounts carry the blank features in every training pass as well as at scoring, only training labels reach the loss and the label-aware features, and a guard asserts every new test account has the identical blank feature vector.
* **But the GAT is not the only way to get this.** A random forest given simple neighbour features matches the GAT on T-Finance (0.955 vs 0.935 AUC, same 72.5% caught) and clearly beats it on Amazon (0.82 vs 0.69).
* Caveats: 3 seeds, small test splits (about 23 anomalous accounts on T-Finance), and links are kept intact for new accounts. The neighbour-filter variant was dropped from this test after one seed (0.72 AUC with no features against 0.95 for skip): its similarity scores are meaningless for a blank account. Each condition takes 40-100 min (`python scripts/cold_start_eval.py tfinance path/to/tfinance --seeds 3 --condition none`; add `--rewire` for the control).

## Hybrid: give the random forest the graph

If simple models are strong and the graph carries signal, the obvious next step is to combine them. `scripts/hybrid_eval.py` adds three kinds of graph features to logistic regression and the random forest, using the same protocols as above (seeds 0-2 on Amazon and T-Finance, Elliptic's temporal split with local features only):
* **(a) neighbour aggregates, label-free:** the mean and max of the neighbours' features, and the log of the number of links;
* **(b) known-fraud neighbours:** how many neighbours are training accounts, how many of those are fraud, and the share. It reads training labels only. It is left out on Elliptic, where links never cross time steps, so a test transaction never has a neighbour with a known label;
* **(c) the GAT's risk score** (edges + skip). This is out-of-fold for training rows: two GATs, each trained on half the training split, score the other half, so the forest never sees a score the GAT produced for an account it was trained on. Validation and test rows are scored by a GAT trained on the full training split.

| T-Finance (3 seeds) | AUC | F1 at 0.5 | Fraud caught, riskiest 5% |
|---|---|---|---|
| Logistic regression | 0.953 ± 0.009 | 0.472 | 76.8% |
| Random forest | 0.943 ± 0.014 | 0.735 | 72.5% |
| GAT + edges, skip (alone) | 0.953 ± 0.037 | 0.641 | 81.2% |
| RF + (a) | 0.964 ± 0.020 | 0.859 | 82.6% |
| **RF + (a) + (b)** | 0.980 ± 0.012 | **0.868** | **85.5%** |
| RF + (c) | 0.976 ± 0.014 | 0.787 | 82.6% |
| RF + (a) + (b) + (c) | 0.975 ± 0.016 | 0.859 | 85.5% |
| **Logistic regression + (a) + (b) + (c)** | **0.983 ± 0.011** | 0.693 | **85.5%** |

| Amazon (3 seeds) | AUC | F1 at 0.5 | Fraud caught, riskiest 5% | Precision, riskiest 5% |
|---|---|---|---|---|
| Logistic regression | 0.960 ± 0.015 | 0.763 | 51.4% | 0.949 |
| **Random forest** | **0.974 ± 0.015** | 0.835 | 52.1% | 0.962 |
| GAT + edges, skip (alone) | 0.968 ± 0.013 | 0.758 | 50.7% | 0.936 |
| RF + (a) | 0.954 ± 0.023 | 0.828 | **53.5%** | **0.987** |
| RF + (a) + (b) | 0.953 ± 0.022 | 0.823 | 53.5% | 0.987 |
| RF + (c) | 0.973 ± 0.017 | **0.840** | 52.1% | 0.962 |
| RF + (a) + (b) + (c) | 0.960 ± 0.017 | 0.828 | 53.5% | 0.987 |
| Logistic regression + (a) + (b) + (c) | 0.958 ± 0.013 | 0.735 | 51.4% | 0.949 |

| Elliptic (seed 0, test steps 35-49, local features) | F1 | Precision | Recall | AUC | Fraud caught, riskiest 5% | Precision, riskiest 5% |
|---|---|---|---|---|---|---|
| Logistic regression | 0.242 | 0.141 | 0.859 | 0.865 | 13.1% | 0.170 |
| Random forest | 0.772 | 0.844 | 0.712 | 0.893 | 69.6% | 0.904 |
| GAT + edges, skip (alone) | 0.507 | 0.379 | 0.765 | 0.898 | 55.3% | 0.718 |
| **RF + (a)** | **0.815** | **0.931** | 0.725 | 0.903 | **72.4%** | **0.940** |
| RF + (c) | 0.784 | 0.851 | 0.727 | 0.892 | 71.0% | 0.922 |
| RF + (a) + (c) | 0.811 | 0.923 | 0.723 | **0.915** | 72.0% | 0.935 |
| Logistic regression + (a) + (c) | 0.430 | 0.293 | 0.802 | 0.904 | 52.5% | 0.682 |
| *Reference (earlier):* RF, all 165 incl. Elliptic's own neighbour aggregates | 0.802 | 0.900 | 0.724 | 0.918 | 72.1% | 0.936 |

What this honestly shows:
* **Where links connect fraud to fraud, the hybrid beats both the random forest and the GAT.** On T-Finance, the forest with neighbour features reaches F1 0.87 (against 0.74 for the forest and 0.64 for the GAT) and catches 85.5% of fraud in the riskiest 5% (against 72.5% and 81.2%). On Elliptic, RF + (a) has the best F1 of any model in this README (0.815), above even the forest on all 165 features with the dataset's own neighbour aggregates (0.802). RF + (a) + (c) has the best AUC on local features (0.915).
* **On Amazon it does not.** The plain random forest is the best model on AUC (0.974, also above the GAT's 0.968), and neighbour features lower its AUC slightly (0.954). They make the very top of the list a little cleaner (98.7% precision in the riskiest 5%, against 96.2%), but these gaps are within noise. This is the camouflage result again.
* **The GAT's score adds little on top of explicit neighbour features.** It helps a forest that has nothing else (T-Finance RF + (c): AUC 0.943 to 0.976), and it lifts logistic regression the most. But once (a) and (b) are in, adding (c) changes nothing beyond noise. So in our tests the cheapest strong design is a tree model with neighbour features; the GAT is the end-to-end alternative.
* Caveats: 3 seeds on Amazon and T-Finance (5 were planned; the runs were cut to fit the time), one on Elliptic. The out-of-fold GAT scores for training rows come from GATs trained on half the data, so they are a little weaker than the scores for test rows. Run time is about 2 hours per dataset on a laptop CPU (`python scripts/hybrid_eval.py tfinance path/to/tfinance --seeds 3`).

**What this means for Sentinel Twin:** the graph matters most for exactly the accounts Sentinel Twin cares about: new accounts that share infrastructure with known fraud. There, feature-only models are blind and link-based models keep most of their accuracy. The strongest practical design in these tests is a tree model fed neighbour features (including the share of known-fraud neighbours), with the GAT as an end-to-end option where per-neighbour attention is wanted. The app still ships the GAT.
