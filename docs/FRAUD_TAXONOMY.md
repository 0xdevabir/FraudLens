# Fraud taxonomy — eight kinds of fraud, and what detects each

The payment models ([MODEL_CARD.md](MODEL_CARD.md)) see a transaction. Most fraud
against wallet customers in Bangladesh starts earlier, in a phone call, an SMS, a
Facebook page or a link, and some of it never shows in the payment at all (a forged
"cash-in" SMS moves no money). This document names the eight kinds of fraud
FraudLens covers, how each one happens here, which part of the platform sees it,
and what was measured.

Everything below is defined in one file, `backend/src/fraudlens/intel/taxonomy.yaml`:
the categories, their Bangladesh context, the advice a customer is shown (fixed
English and Bangla text, like the policy's messages), and how older names in the
platform (scenarios, typologies, report categories) map onto the eight.

## 1. Four detectors

| Detector | What it is | Model? |
| --- | --- | --- |
| **Payment models** | The transaction and recipient models, the rules and the payment graph (MODEL_CARD, DECISION_POLICY). They decide the tier of a payment. | LightGBM + rules |
| **Message classifier** | Reads a message a customer pastes ("is this a scam?") and names the kinds of fraud it looks like. `fraudlens.intel.text` | logistic regression on character n-grams and cue patterns |
| **Link check** | Looks at every link in a message: a wallet's name on a domain that is not the wallet's, digit-for-letter look-alikes (`bka5h`), a bare IP address, an `.apk` download, a hidden host (`upaybd.com@elsewhere…`), a link shortener. `fraudlens.intel.links` | no: fixed checks |
| **Ledger check** | "They say they paid me": looks the payment up in the recorded transactions. `fraudlens.intel.proof` | no: a lookup |

Only the payment models decide anything about money. The other three give advice:
nothing is blocked, opened or flagged because of their answer.

## 2. The eight categories

| # | Category | How it happens in Bangladesh | Seen by |
| --- | --- | --- | --- |
| 1 | **Social engineering** — fake support call, OTP theft, PIN theft, romance scam, refund scam | A call "from the bKash/Nagad/upay office": the account will be closed unless the OTP that just arrived is read out. A refund that needs the customer's code first. A relative "in hospital" on a new number. Long chats on Facebook, IMO or WhatsApp that end with a gift stuck at customs. | payment models, message classifier |
| 2 | **Fake identity and impersonation** — fake customer care, fake agent, fake support page, fake merchant | "Customer care" numbers and Facebook pages that copy a wallet's logo and answer public complaints. Callers posing as the agent just used, a bank officer, or an education-board or allowance (উপবৃত্তি, ভাতা) official. The number is almost always a personal mobile. | payment models, message classifier, link check |
| 3 | **Fake payment and transaction** — fake screenshot, fake SMS, fake refund, transaction manipulation | "Bhai, vul kore apnar number e taka chole geche, ferot din": an SMS copying a cash-in notice, then a call asking for the money back. Shopkeepers and F-commerce sellers shown an edited "successful payment" screenshot. | ledger check, message classifier |
| 4 | **Account and identity takeover** — OTP theft, PIN theft, SIM fraud, stolen phone | The OTP and PIN from a scam call are used to register the wallet on the fraudster's handset; the balance leaves within minutes to a mule and out at an agent in another district. Also SIM replacement with someone else's papers, stolen phones, remote-access apps. | payment models, message classifier |
| 5 | **Marketplace and merchant fraud** — fake e-commerce, marketplace scam, advance payment, fake merchant | Facebook-page shops and listings that take the price or a "booking"/"delivery charge" in advance to a personal wallet, then block the buyer. Deep-discount pre-order schemes. Second-hand phones and motorbikes with a courier fee first. | message classifier, payment models |
| 6 | **Phishing and malicious technology** — phishing links, malicious APK, fake QR, remote access | Links to copies of a wallet's login or "KYC update" form. "Update" apps sent as `.apk` files. Callers asking the customer to install AnyDesk or TeamViewer. QR codes said to *receive* money that open a payment. | link check, message classifier |
| 7 | **Transaction and financial network fraud** — mule accounts, layered transfers, rapid cash-out | Mule wallets opened with other people's NIDs or rented from students and day labourers for a commission. Money from many victims lands in one wallet, hops once or twice and is cashed out at agents within minutes. The same pattern carries betting and hundi money. | payment models, message classifier (recruitment offers) |
| 8 | **Scam campaigns** — lottery, investment, job, loan, charity, fake cashback, fake remittance | "Apni 25 lakh taka lottery jitechen" with a fee first. Trading and MLM apps with daily returns. Telegram "like and subscribe" jobs that ask for a deposit. Loan apps that charge a processing fee and never lend. Stipend and remittance-bonus messages, charity appeals after floods and in Ramadan. | payment models, message classifier |

What each detector contributes per category:

- **1, 2, 8** reach the payment models as a first payment to a stranger, a
  receiver many unrelated people started paying, or rising amounts to one new
  wallet. That part is unchanged and measured in MODEL_CARD. New here: the
  message itself can be checked before any money moves.
- **3** cannot be detected by reading the proof. A forged cash-in SMS is made to
  read exactly like a real one. So the answer comes from the ledger: is there a
  completed transaction of that amount **to the asking wallet**? The classifier
  only adds a second sign, the request that follows ("send it back").
- **4** is the payment models' takeover scenario (new handset, new district,
  emptied balance), plus messages that ask for a code, a SIM change or a device
  registration.
- **5** is mostly text: the simulator has no merchant fraud, so the payment
  models were never measured on it (§6).
- **6** is mostly the link check, which needs no training and still works when the
  classifier is not loaded.
- **7** is the recipient (mule) model, the graph and the rings. The classifier
  adds only the recruitment side: offers to rent a wallet or "receive and forward".

## 3. Where it appears

| Where | What |
| --- | --- |
| `POST /v1/customer/message-check` | `{wallet_id, text}` → `level` (`none`, `caution`, `high`), `risk`, the categories it looks like, the cues that matched with the exact phrases, the words that weighed most (`highlights`), each link and what is wrong with it, and the fixed advice in English and Bangla. 20 a minute per wallet. A flagged check makes a later payment to the number or amount it named a warning (§4). |
| `POST /v1/customer/payment-verify` | `{wallet_id, txn_id?, amount?, message?}` → `verified`, `mismatch` or `not_found`, the checks behind it, and a fixed message. The ID and amount are read from the pasted message when not given. 10 a minute per wallet. |
| `GET /v1/decisions/{txn_id}` | `fraud_categories`: the categories an alert belongs to and what that rests on (`scenario`, `similar_cases`, `mule_score`). |
| `GET /v1/cases/{id}` | each customer report carries `fraud_categories`, from the category the customer picked or, for "something else", from the description. |
| `POST /v1/customer/reports` | four more categories to pick: fake payment proof, online seller, link or app, job or loan. |
| `GET /v1/intel/taxonomy` | the taxonomy, whether the classifier is serving, and its evaluation report. |
| Console → **Fraud types** | the eight categories with their measured results; "Check a message" and "Verify a payment" against the demo endpoints. |

Rules that hold for all of them:

- **The text is not kept.** A checked message is scored and dropped. The demo
  endpoints write the level and the category names to the audit log, never the text.
- **Customer text is never an instruction.** It is input to a classifier and to
  regular expressions; nothing in it is executed or passed to a language model.
- **A wallet can verify only payments made to it.** Asking about anyone else's
  transaction gives the same `not_found` as one that does not exist, so the
  endpoint cannot be used to look up other people's payments.
- **Labels decide nothing.** The category on an alert or a report is for the
  reviewer. It changes no tier, flags nobody and releases nothing.
- **A classifier trained on other cues is not served.** If `cues.yaml` changed
  since training, the API runs with the link check only until `make intel` is run.

## 4. The message classifier

**Two sources of text.**

- *Our own scripts.* `intel/corpus.yaml` holds 413 templates in 60 scripts
  ("families"), 42 of them scams, in Bangla script (`bn`), Banglish (`bl`, Bangla in
  Latin letters, how most SMS and chat is written), English (`en`) and code-mixed
  Bangla with English words (`mx`, "OTP টা বলুন"). Every line was written for this
  project; none is a real message. Slots are filled with generated names, amounts
  and numbers: 10,566 messages (bl 3,386, bn 2,182, en 3,612, mx 1,386). The
  harmless side is deliberately hard: real OTP notices that say "never share this
  code", cash-in receipts, delivery messages with tracking links, a parent asking
  for money on a mobile wallet.
- *A public corpus.* "Bengali SMS Smishing Dataset", Hugging Face
  `shariul-islam/bengali-sms-smishing-dataset`, **MIT licence**, 7,005 SMS in the
  same four varieties, labelled `smish`, `promo` or `normal` by its authors. It is
  fetched at a pinned revision (`9d7c131c…`) and checked against fixed SHA-256 sums
  (`intel/external.py`); nothing from it is committed. `smish` counts as a scam,
  the other two as harmless. It has no category labels, so it trains the
  scam-or-not output only. Its own split is kept: 4,903 train, 701 validation,
  1,401 test, and the test part is never trained on. Copies of the same dataset
  re-uploaded under other accounts were not used; the original is.

**Typologies.** Every scam script is tagged with the shape customers meet most
often: fake agent or helpline, prize or lottery, wrong-number send-back, job or
investment, fake government aid, OTP or PIN phishing, or `other`. Each of the six
has trained scripts *and* at least one held-out script.

**Three test sets.**

| | What it measures | Messages |
| --- | --- | --- |
| `test` | new wording of scripts it was trained on (templates never seen) | 1,591 |
| `unseen` | 17 whole scripts never trained on, at least one per typology (14 scam, 3 harmless) | 1,950 |
| `external` | the public corpus's test split: text written by other people | 1,401 |

**Model.** Logistic regression over character n-grams (2–5, TF-IDF, word-bounded)
plus 27 hand-written cue patterns (asks for a secret, threat, urgency, pay first,
windfall, …) and their pairwise products. Nine outputs, one regression each: scam
or not, and the eight categories; rows without a category label (the public
corpus) are left out of the category outputs. Text is normalised first (Bangla
digits to ASCII, links to one token, digits to `0`). Thresholds are set on the
harmless validation messages of each source separately and the stricter one is
kept: `caution` flags ≤ 3% of them, `high` ≤ 0.5%. Training takes about nine seconds
(`make intel`); without network access it trains on our scripts alone, which is
the previous recipe.

**Why this model.** `make intel-compare` trains five candidates on the same
splits, each on our scripts alone and on our scripts plus the public corpus
(`+ext`), with the same threshold rule. Macro-F1 is over scam and harmless at the
`caution` threshold; latency is one message at a time on a laptop CPU (Apple
silicon, 200 calls).

| Candidate | `test` macro-F1 | `unseen` macro-F1 | `unseen` recall / false alarms | `external` AUC | `external` macro-F1 | p50 / p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| Cue patterns only | 0.777 | 0.518 | 0.439 / 0.0% | 0.701 | 0.521 | 0.39 / 0.47 |
| Cue patterns only +ext | 0.937 | 0.601 | 0.614 / 15.5% | 0.929 | 0.745 | 0.37 / 0.44 |
| Word TF-IDF | 0.526 | 0.377 | 0.249 / 0.0% | 0.714 | 0.463 | 0.22 / 0.25 |
| Word TF-IDF +ext | 0.762 | 0.571 | 0.517 / 0.6% | 0.996 | 0.917 | 0.27 / 0.36 |
| Character n-grams | 0.563 | 0.320 | 0.180 / 0.0% | 0.755 | 0.432 | 0.35 / 0.44 |
| Character n-grams +ext | 0.861 | 0.643 | 0.648 / 8.4% | 0.997 | 0.949 | 0.33 / 0.42 |
| Character n-grams + cues (previous recipe) | 0.735 | 0.525 | 0.450 / 0.0% | 0.802 | 0.495 | 0.76 / 0.85 |
| **Character n-grams + cues +ext (served)** | **0.977** | **0.811** | **0.855 / 7.2%** | 0.996 | **0.972** | 0.72 / 0.86 |
| MiniLM embeddings + LR | 0.494 | 0.286 | 0.165 / 13.4% | 0.821 | 0.502 | 8.9 / 11.5 |
| MiniLM embeddings + LR +ext | 0.626 | 0.459 | 0.398 / 14.3% | 0.969 | 0.746 | 9.7 / 11.9 |

MiniLM is `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers), frozen,
with a logistic regression on its 384-number embedding; it is an optional extra
(`uv sync --extra transformer`) and never a dependency of the service. It lost on
every test set and is 12–14× slower. The likely reason: Banglish is written in
Latin letters the embedding model reads as noise, and the scam signal sits in
short phrases ("code ta bolun") a sentence embedding averages away.

Choice: **character n-grams + cues, trained with the public corpus.** It is best
on all three test sets in macro-F1 and stays under a millisecond. The fallbacks
are the same model trained on our scripts alone (no network at training time) and,
when no classifier is loaded or `cues.yaml` changed since training, the link check
alone.

**Why it says so.** Each matched cue now carries the exact phrase(s) that matched,
as `{text, start, end}` spans of the customer's own text, in whichever script it
was written: "ekhoni OTP code ta bolun", "ওটিপি কোডটি বলুন", "OTP টা বলুন". A flagged
message also returns up to five `highlights`: the words that pushed the score up
most, read from the character n-gram weights. Both are additive fields; every
earlier field of `message-check` is unchanged. The cue patterns gained two
code-mixed forms (an English secret word followed by a Bangla verb, an English fee
word followed by a Bangla "send"); they were written by the same author as the
rest.

**The message and the payment.** When a check is flagged, the wallet that asked
is remembered for 30 minutes with only the wallet IDs, phone numbers and amounts
the message contained (at most five messages a wallet), never the text. A payment
that wallet then makes **to one of those numbers, or for one of those amounts
(±1 Tk)**, is raised from `allow` to `warn` with the reason "follows a message
flagged as a likely scam" in English and Bangla; a payment the policy already
stopped keeps its tier and gains the reason. The recipient check says the same
before the amount is typed. The memory lives in the scoring process and is lost
on restart; with several API processes, only the one that served the check knows.

## 5. Measured

From `backend/artifacts/intel/report.json` (seed 7, model `v2`), which
`make intel` writes and the console page reads, and
`backend/artifacts/intel/compare.json` for the comparison above.

| Served model | `test` (1,591) | `unseen` (1,950) | `external` (1,401) |
| --- | --- | --- | --- |
| ROC-AUC | 0.994 | 0.971 | 0.996 |
| Macro-F1 at `caution` | 0.977 | 0.811 | 0.972 |
| Scam messages flagged (`caution`) | 96.9% | 85.5% | 97.0% |
| Harmless messages flagged (`caution`) | 0.36% | **7.2%** (hard look-alikes 9.3%) | 2.5% |
| Scam messages at `high` | 84.3% | 48.4% | 80.6% |
| Harmless messages at `high` | 0.0% | 0.0% | 0.48% |

The model this replaces, on the same `external` test set: AUC 0.815, macro-F1
0.659, 39.6% of scams flagged, 7.6% of harmless messages flagged.

By language, macro-F1 at `caution`:

| | Bangla | Banglish | English | Code-mixed |
| --- | --- | --- | --- | --- |
| `unseen` | 0.717 | 0.738 | 0.833 | 0.995 |
| `external` | 0.977 | 0.980 | 0.964 | 0.966 |

On `unseen`, scam flagged / harmless flagged: Bangla 78.0% / 0.0%, Banglish
86.7% / **28.8%**, English 85.0% / 0.0%, code-mixed 100% / 1.6%.

By typology on `unseen` (that typology's scams against all harmless messages of
the split), macro-F1 and share of its scams flagged:

| Typology | macro-F1 | flagged |
| --- | --- | --- |
| Fake agent or helpline | 0.937 | 98.6% |
| Prize or lottery | 0.935 | 97.9% |
| Wrong-number send-back | 0.830 | 71.9% |
| Job or investment | 0.943 | 100% |
| Fake government aid | 0.936 | 99.2% |
| OTP or PIN phishing | 0.894 | 85.4% |
| Other | 0.835 | 78.4% |

Held-out scripts, share flagged: bank card 100%, job deposit 100%, utility-bill
link 100%, government aid card 99%, helpline search 99%, prize quiz 98%, eSIM
porting 92%, P2P mule 84%, overpayment 83%, customs parcel 79%, rental and ticket
69%, OTP over social media 69%, wrong recharge 64%, charity visa 63%. Harmless:
split bill 0%, school notice 0%, **official notice 21%**.

How to read this:

- **The public corpus is what moved the numbers.** Every candidate gained from
  it, on our held-out scripts as well as on the external test. Text written by
  other people taught the model spellings ours did not have.
- **The `external` numbers are probably flattering.** The corpus is fairly
  uniform in style and about 4% of its test messages also appear in its training
  split. Treat 0.97 as an upper bound for text of that kind, not a forecast.
- **The cost is false alarms on scripts it has never seen**: 7.2% of harmless
  `unseen` messages, almost all Banglish, mostly the official-notice script (a
  real bank or office telling you about a deadline). The model trained on our
  scripts alone flagged none of them but caught only 45% of unseen scams. A
  `caution` shows advice, it blocks nothing; `high` raised no harmless message on
  either of our test sets.
- **Wrong-number send-back is the hardest typology** (72% flagged): the message
  is short and polite and asks for nothing secret. The message-to-payment link is
  aimed at exactly this case: the amount and number it names are what gets paid.
- **Tuning disclosure.** After the first run on `test`, two kinds of harmless text
  were too often flagged (a family member asking for money in Banglish, a
  delivery notice with a tracking link), and eight harmless templates of those
  kinds were added. `test` is therefore not fully untouched. `unseen` and
  `external` were not used for any change.

The ledger check has no accuracy figure: it is exact against the ledger by
construction. Its behaviour, the cue spans, the highlights and the
message-to-payment link are covered by tests (`test_intel.py`,
`test_platform.py`).

## 6. Limits, stated plainly

- **Our corpus is synthetic and its author wrote the cues.** Templates were
  written for this project by the same person who wrote the cue patterns, with the
  trained scripts in view. The held-out scripts were not used to tune them, but
  real messages will match less often than these numbers suggest. The public
  corpus is real text written by other people, but it is one dataset, of
  uncertain label quality, with near-duplicates across its splits. Before use it
  needs real, consented, labelled messages from this platform's customers.
- **Banglish false alarms on new scripts.** 29% of harmless Banglish messages in
  `unseen` reached `caution`. Watch this first in shadow use.
- **The message-to-payment link is in memory.** It is lost on restart and not
  shared between API processes; a shared store would be needed to run more than
  one. It matches numbers and amounts only, so a scam message that names neither
  does not raise the payment.
- **A forged cash-in SMS cannot be told from a real one by its text.** Category 3
  rests on the ledger check, and that only helps a customer who asks.
- **Text only.** No OCR of screenshots, no voice calls, no QR images. A fake QR
  code is covered only by the advice and by the payment models once money moves.
- **No SIM-swap or device-integrity signal.** Category 4 sees the new handset and
  district on the payment, not the SIM replacement itself; that needs operator data.
- **No merchant fraud in the simulator.** Categories 5 and 6 have no ground truth
  in the transaction data, so the payment models are unmeasured on them. They may
  still fire on the many-strangers-one-wallet pattern, but that is not claimed.
- **The link check does not open links.** It does not know a domain's age,
  reputation or content, and its list of real brand domains is configuration
  (three brands) to be kept by whoever runs the platform. A scam on an unrelated,
  ordinary-looking domain passes it.
- **Alert categories are a mapping, not a measurement.** They are read from the
  scenario, the similar past cases and the mule score. How often they match the
  simulator's ground truth was not measured.
- **The Bangla texts were written by the developers**, as elsewhere.
