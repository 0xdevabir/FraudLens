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
| `POST /v1/customer/message-check` | `{wallet_id, text}` → `level` (`none`, `caution`, `high`), `risk`, the categories it looks like, the cues that matched, each link and what is wrong with it, and the fixed advice in English and Bangla. 20 a minute per wallet. |
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

**Corpus.** No real customer message was used. `intel/corpus.yaml` holds 300
templates in 51 scripts ("families"), 36 of them scams, in English, Bangla and
Banglish (Bangla in Latin letters, how most SMS and chat is written). Slots are
filled with generated names, amounts and numbers to make 7,572 messages. The
harmless side is deliberately hard: real OTP notices that say "never share this
code", cash-in receipts, delivery messages, friends asking for money.

**Splits.** Test messages come from *templates* the model never saw, so the test
measures new wording, not memorised sentences. Eleven whole scripts (8 scam, 3
harmless) are kept out of training entirely as the `unseen` split: customs parcel,
bank card, eSIM porting, overpayment, P2P mule, rental and ticket, charity visa,
utility-bill link.

**Model.** One-vs-rest logistic regression over character n-grams (2–5, TF-IDF)
plus 27 hand-written cue patterns (asks for a secret, threat, urgency, pay first,
windfall, …) and their pairwise products. Nine outputs: scam or not, and the eight
categories. Text is normalised first (Bangla digits to ASCII, links to one token,
digits to `0`), so the model learns shapes, not numbers. Thresholds are set on the
validation split: `caution` at ≤ 3% of harmless messages flagged, `high` at ≤ 0.5%.
Training takes about four seconds (`make intel`).

**Why it says so.** The reasons returned are the cues that matched, each with a
fixed English and Bangla label.

## 5. Measured

From `backend/artifacts/intel/report.json` (seed 7), which `make intel` writes
and the console page reads.

| | New wording of trained scripts (`test`, 1,277 messages) | Scripts never seen (`unseen`, 1,089 messages) |
| --- | --- | --- |
| ROC-AUC | 0.989 | 0.922 |
| Scam messages flagged, classifier alone | 86.8% | 68.2% |
| Scam messages flagged, with the link check | **90.2%** | **81.0%** |
| Harmless messages flagged | 0.23% (hard look-alikes: 0.34%) | 0.0% |

By language, classifier alone (scam flagged / harmless flagged):

| | Banglish | Bangla | English |
| --- | --- | --- | --- |
| `test` | 81.3% / 0.56% | 91.8% / 0.0% | 92.8% / 0.0% |
| `unseen` | 71.1% / 0.0% | 82.4% / 0.0% | 60.0% / 0.0% |

Naming the category (a message can have several):

| Category | `test` support | precision | recall | `unseen` support | precision | recall |
| --- | --- | --- | --- | --- | --- | --- |
| 1 Social engineering | 420 | 0.997 | 0.798 | 183 | 0.394 | 0.579 |
| 2 Impersonation | 394 | 0.912 | 0.893 | 81 | 0.421 | 0.951 |
| 3 Fake payment | 50 | 1.000 | 0.820 | 106 | 1.000 | 0.566 |
| 4 Account takeover | 233 | 0.683 | 0.768 | 159 | 1.000 | 0.843 |
| 5 Merchant fraud | 113 | 0.934 | 0.504 | 97 | 0.151 | 0.134 |
| 6 Phishing and malware | 158 | 1.000 | 0.804 | 119 | – | 0.000 |
| 7 Financial network | 77 | 0.825 | 0.857 | 100 | – | 0.000 |
| 8 Scam campaign | 289 | 0.817 | 0.882 | 239 | 0.252 | 0.167 |

Held-out scripts, share flagged by the classifier alone: customs parcel 99%, bank
card 95%, overpayment 84%, charity visa 83%, eSIM porting 73%, P2P mule 70%,
rental and ticket 53%, utility-bill link **0%**. The three harmless held-out
scripts: 0%.

How to read this:

- **Spotting a scam generalises; naming it does not.** On scripts it never saw the
  model still flags most scam messages with no false alarms, but puts them in the
  wrong category more often than the right one for 5, 6, 7 and 8. When no category
  is clear the customer is shown the general advice instead.
- **The utility-bill link script is missed entirely by the classifier** and caught
  by the link check. That is why the two run together, and why the combined row
  is the one that matters for category 6.
- **Banglish is the weakest language on trained scripts** (spelling varies most),
  and English on unseen ones.
- **Merchant fraud is the weakest category**: half named correctly on new wording,
  13% on new scripts. An advance-payment request reads much like an honest seller's.

The ledger check has no accuracy figure: it is exact against the ledger by
construction. Its behaviour is covered by tests (`test_intel.py`,
`test_platform.py`): right receiver, wrong amount, not completed, someone else's
payment, a non-numeric ID.

## 6. Limits, stated plainly

- **The corpus is synthetic and its author wrote the cues.** Templates were
  written for this project by the same person who wrote the cue patterns, with the
  trained scripts in view. The held-out scripts were not used to tune them, but
  real messages will match less often than these numbers suggest. Before use it
  needs real, consented, labelled messages.
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
