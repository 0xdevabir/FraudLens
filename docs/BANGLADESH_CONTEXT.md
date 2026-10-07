# Bangladesh context: the problem in numbers

FraudLens's model results come from synthetic data (DATA_ASSUMPTIONS.md). This
page covers the real problem they stand in for. Every figure is from a public
source, linked below and accessed on **7 October 2026**. "Derived" means we did
the arithmetic ourselves from the sourced figures. Where no public source could
be found, we say so.

## 1. The problem in numbers

### Scale of MFS

| Figure | Value | Source |
| --- | --- | --- |
| Registered MFS accounts, Feb 2025 | 239.24 million (2,392.40 lakh) | [S1] |
| Active accounts, Feb 2025 | 87.15 million (derived: 36.4% of registered) | [S1] |
| Agents, Feb 2025 | 1,856,190 | [S1] |
| Providers | 13 | [S1] |
| Transactions, Feb 2025 | 671.3 million, worth Tk 164,726 crore (≈ Tk 1.65 trillion) | [S1] |
| Average transaction, Feb 2025 | ≈ Tk 2,454 (derived) | [S1] |
| Transactions, Oct 2025 | 678.63 million, worth Tk 1.58 trillion | [S2] |
| Average P2P send / cash-out / cash-in, Oct 2025 | Tk 3,552 / Tk 2,132 / Tk 2,898 (derived) | [S2] |
| Account holders by sex, Oct 2025 | 56% male, 44% female | [S2] |

### Fraud and what happens to victims

| Figure | Value | Source |
| --- | --- | --- |
| Payment fraud reported to Bangladesh Bank, 2025 | Tk 926.01 million across 81,423 cases; 10.7% recovered | [S3] |
| MFS share of it | Tk 813.26 million (88% of fraud value); only 8.7% recovered, Tk 742.56 million lost | [S3] |
| Personal MFS holders who were fraud victims | 6.3% (agents 17.0%, merchants 1.6%) | [S4] |
| Personal holders who lost money | 3.6% | [S4] |
| Personal losses reported | Tk 300 to Tk 83,000 | [S4] |
| How the fraud happened (multiple answers) | deceptive or false information 52.6%; phone call or SMS 42.1%; hacking 12.3% | [S4] |
| Victims who went to the police (case or GD) | 7.6% | [S4] |
| Holders with a problem who did not complain | 58.8%; the main reason was "no benefit" (65.8%) | [S4] |
| Holders aware of Bangladesh Bank's complaint centre (CIPC) | 6.2% | [S4] |
| Suspicious transaction/activity reports from MFS to BFIU | 451 (2022), 1,134 (2023), 1,107 (2024) | [S4] |
| Users who had experienced MFS fraud, 2021 survey of 9,279 | 9.3% ("1 in 10"); average loss above Tk 9,000; agents ≈ 13% | [S5] |
| Main fraud types in that survey | compromised PINs and impersonation | [S5] |

The two surveys measure different things (any fraud in 2021 vs fraud victims
in the TIB sample, Nov 2023–May 2025), so they agree in size but should not be
read as a trend.

### No public source found

Per-provider scam loss rates; the share of scam money that is cashed out
within minutes; how long mule wallets stay active; the split of scams by
district; false-positive tolerance of Bangladeshi customers. These are what
the validation kit (docs/validation/) is designed to collect.

## 2. Stakeholder map

| Stakeholder | What they need | What FraudLens gives them |
| --- | --- | --- |
| **MFS customers** (239M accounts; 44% women) | Not to lose money; not to be blocked on a normal payment | Warn / step-up / hold before money leaves, in plain language; never a refused payment (DECISION_POLICY.md §2) |
| **MFS providers** (built for upay; applicable across Bangladesh's 13 licensed MFS) | Lower fraud loss and complaint volume; regulatory compliance; low friction | Real-time scoring with reasons, analyst queue, mule network view, decision audit trail |
| **Agents** (1.86M; 17% have been fraud victims [S4]) | Protection from being used for cash-out; commission income | Agent risk score separating colluding/farming agents from transit hubs |
| **Bangladesh Bank** (PSD, FICSD, CIPC) | Providers that monitor patterns and resolve complaints in time | Monitoring that §10.1(ii) of the 2022 regulations asks for; case records kept and exportable |
| **BFIU** | Better suspicious-transaction reports | Case pages and network evidence that can back an STR |
| **Police / CID** | Evidence that follows the money | Mule chains and shared handsets across cases |

## 3. Regulatory fit

- **Bangladesh MFS Regulations 2022** (issued 15 Feb 2022) [S6]:
  - §10.1(ii) — providers must monitor transaction patterns to identify
    unauthorised or suspicious activity. FraudLens is that monitoring, in real
    time.
  - §15 — providers must run awareness programs "to combat against fraud and
    forgery". The warn screen is awareness at the moment it matters.
  - §17.3 — a 24-hour call centre and each dispute resolved within 10 working
    days. The hold tier puts a human review on a 30-minute target.
  - §17.4 — customers may complain to CIPC; §18.1 — records kept at least 6
    years. Every decision is logged with its model, policy version and reasons.
- **Bangladesh Bank, November 2025** — ordered the 13 MFS operators to deploy
  "artificial intelligence–based systems to detect and flag illegal
  transactions in real time" (issued about online gambling) [S7].
- **TIB recommendations, May 2025** [S4] — an AI policy for suspicious
  transaction reporting (#3), a central blacklist database (#9), a toll-free
  CIPC line and a standard complaint process (#12–13). TIB also notes the
  absence of proper use of AI in the sector.
- **The UK comparison** — since 7 October 2024 UK payment firms must reimburse
  authorised-push-payment scam victims up to £85,000, split 50:50 between the
  sending and receiving firm [S8]. In the first year 88% of money lost was
  reimbursed (66% the year before) and 84% of claims were settled within 5
  business days [S9]. Bangladesh has no equivalent: 8.7% of MFS fraud value was
  recovered in 2025 [S3]. If liability ever shifts to providers, catching the
  payment before it leaves is far cheaper than paying it back.

## 4. Pitch openers

Each line is backed by the sources above.

1. "In 2025, Bangladesh Bank recorded Tk 81 crore stolen through mobile
   financial services — and only 8.7% of it came back." [S3]
2. "One in sixteen MFS users has been a fraud victim, and only 7.6% of them
   ever went to the police." [S4]
3. "239 million MFS accounts move about Tk 1.6 trillion a month. Fraud
   prevention today starts after the money is gone." [S1][S2][S3]
4. "Most MFS fraud is not hacking — 52.6% is deception. The customer types
   their own PIN. Only a check before the payment can stop it." [S4]
5. "The UK now returns 88% of scam losses to victims. Bangladesh returns 8.7%.
   Bangladesh Bank has already asked operators for real-time AI detection; this
   is what that looks like." [S3][S7][S9]

## Sources

All accessed 7 October 2026.

- **[S1]** Bangladesh Bank, *Mobile Financial Services (MFS) comparative
  summary statement* (January and February 2025).
  https://www.bb.org.bd/en/index.php/financialactivity/mfsdata
- **[S2]** The Financial Express, "MFS transactions maintain rising trend in
  Oct '25", 3 January 2026, citing Bangladesh Bank.
  https://thefinancialexpress.com.bd/trade/mfs-transactions-maintain-rising-trend-in-oct-25
- **[S3]** The Financial Express, "Fraudsters gobble up Tk 926m in 2025",
  16 June 2026, citing Bangladesh Bank.
  https://thefinancialexpress.com.bd/trade/fraudsters-gobble-up-tk-926m-in-2025
- **[S4]** Transparency International Bangladesh, *Governance Challenges in
  the Mobile Financial Services Sector and Way Forward* (executive summary),
  27 May 2025. Survey of 1,784 personal account holders, 664 agents and 429
  merchants in 32 districts, November 2023–May 2025.
  https://www.ti-bangladesh.org/images/2025/report/mfs/Executive-Summary-Mobile-Financial-Services-Sector-En.pdf
- **[S5]** Policy Research Institute survey (Aug–Sep 2021, 9,279 respondents),
  reported in Dhaka Tribune, 30 March 2022.
  https://www.dhakatribune.com/business/266888/1-out-of-every-10-are-victims-of-mfs-fraud
  and The Business Standard, https://www.tbsnews.net/node/394218
- **[S6]** Bangladesh Bank, *Bangladesh Mobile Financial Services (MFS)
  Regulations, 2022*.
  https://www.bb.org.bd/aboutus/regulationguideline/psd/mfs_regulations_2022.pdf
- **[S7]** Yogonet, "Bangladesh Bank orders mobile financial service providers
  to curb online gambling transactions", 6 November 2025.
  https://www.yogonet.com/international/news/2025/11/06/116204-bangladesh-bank-orders-mobile-financial-service-providers-to-curb-online-gambling-transactions
- **[S8]** Payment Systems Regulator, *APP scams*,
  https://www.psr.org.uk/our-work/app-scams/ ; DLA Piper, "PSR confirms APP
  fraud reimbursement level to be reduced", 27 September 2024.
  https://dlapiper.com/en-th/insights/publications/2024/09/psr-confirms-app-fraud-reimbursement-level-to-be-reduced
- **[S9]** Payment Systems Regulator, "One year on: impact of APP
  reimbursement on victims", 8 October 2025.
  https://www.psr.org.uk/news-and-updates/latest-news/news/one-year-on-impact-of-app-reimbursement-on-victims/
- **[S10]** Dhaka Tribune, "bKash raises daily, monthly account transaction
  limits", 29 March 2025 (Bangladesh Bank PSD notice of 27 March 2025).
  https://www.dhakatribune.com/business/377554/bkash-raises-daily-monthly-account-transaction
