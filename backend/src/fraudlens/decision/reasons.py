"""Turn feature attributions into reasons a person can read, in English and Bangla.

A reason is a topic (for example "how fast the receiving wallet moves money on")
with the facts behind it. Every sentence is rendered from the actual feature
value, so a reason can never state something the data does not show. Which
topics are listed, and in what order, comes from the model's exact SHAP
contributions for this transaction.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..features import FEATURES

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
_BN_UNITS = {"sec": "সেকেন্ড", "min": "মিনিট", "hours": "ঘণ্টা", "days": "দিন"}


def bn_digits(text: str) -> str:
    return text.translate(_BN_DIGITS)


def _duration(seconds: float) -> tuple[str, str]:
    if seconds < 90:
        n, unit = f"{seconds:.0f}", "sec"
    elif seconds < 90 * 60:
        n, unit = f"{seconds / 60:.0f}", "min"
    elif seconds < 48 * 3600:
        n, unit = f"{seconds / 3600:.1f}", "hours"
    else:
        n, unit = f"{seconds / 86400:.0f}", "days"
    return f"{n} {unit}", f"{bn_digits(n)} {_BN_UNITS[unit]}"


def render(kind: str, v: float) -> tuple[str, str]:
    """A feature value as display text (English, Bangla)."""
    if kind == "secs":
        return _duration(v)
    if kind == "days":
        return _duration(v * 86400)
    if kind == "taka":
        text = f"৳{v:,.0f}"
    elif kind == "count":
        text = f"{v:,.0f}"
    elif kind == "pct":
        text = f"{v * 100:.0f}%"
    elif kind == "times":
        text = f"{v:.1f}×"
    elif kind == "rate":
        text = f"{v:.1f}" if v >= 1 else f"{v:.2f}"
    elif kind == "signed":
        text = f"{v:+.1f}"
    elif kind == "hour":
        text = f"{int(v):02d}:00"
    elif kind == "kmh":
        text = f"{v:,.0f}"
        return f"{text} km/h", f"{bn_digits(text)} কিমি/ঘণ্টা"
    else:
        raise ValueError(f"unknown value kind {kind!r}")
    return text, bn_digits(text)


@dataclass(frozen=True)
class Phrase:
    group: str
    kind: str  # how the value is rendered; "flag" has no value in the text
    en: str
    bn: str
    en_zero: str = ""  # used instead when the value is 0 (flags and counts)
    bn_zero: str = ""


# One entry per feature. {v} is the rendered value.
PHRASES: dict[str, Phrase] = {
    "amount": Phrase("AMOUNT", "taka", "the amount is {v}", "লেনদেনের পরিমাণ {v}"),
    "amount_to_balance": Phrase(
        "AMOUNT", "pct", "it is {v} of the sender's balance", "এটি প্রেরকের ব্যালেন্সের {v}"
    ),
    "balance_after": Phrase(
        "AMOUNT", "taka", "{v} would be left in the wallet", "ওয়ালেটে {v} অবশিষ্ট থাকবে"
    ),
    "s_amount_z": Phrase(
        "AMOUNT",
        "signed",
        "it differs from the sender's usual amount by {v} standard deviations",
        "প্রেরকের স্বাভাবিক পরিমাণ থেকে এটি {v} স্ট্যান্ডার্ড ডেভিয়েশন আলাদা",
    ),
    "s_amount_vs_max": Phrase(
        "AMOUNT",
        "pct",
        "it is {v} of the largest amount the sender has moved before",
        "এটি প্রেরকের আগের সর্বোচ্চ লেনদেনের {v}",
    ),
    "hour": Phrase("TIME_AND_PLACE", "hour", "made at {v}", "লেনদেনের সময় {v}"),
    "s_hour_share": Phrase(
        "TIME_AND_PLACE",
        "pct",
        "{v} of the sender's activity happens around this hour",
        "প্রেরকের {v} লেনদেন দিনের এই সময়ে হয়",
    ),
    "s_away_from_home": Phrase(
        "TIME_AND_PLACE",
        "flag",
        "made outside the sender's home district",
        "প্রেরকের নিজ জেলার বাইরে থেকে করা",
        "made from the sender's home district",
        "প্রেরকের নিজ জেলা থেকে করা",
    ),
    "s_district_changed": Phrase(
        "TIME_AND_PLACE",
        "flag",
        "the district differs from the sender's previous transaction",
        "আগের লেনদেনের চেয়ে জেলা ভিন্ন",
        "same district as the sender's previous transaction",
        "আগের লেনদেনের একই জেলা",
    ),
    "s_travel_kmh": Phrase(
        "TIME_AND_PLACE",
        "kmh",
        "to be here after the previous transaction the sender had to travel at {v}",
        "আগের লেনদেনের পর এখানে পৌঁছাতে প্রেরককে {v} গতিতে যেতে হতো",
        "no travel was needed since the sender's previous transaction",
        "আগের লেনদেনের পর প্রেরকের যাতায়াতের প্রয়োজন হয়নি",
    ),
    "s_age_days": Phrase(
        "SENDER_HISTORY",
        "days",
        "the sender's wallet was opened {v} ago",
        "প্রেরকের ওয়ালেট {v} আগে খোলা হয়েছে",
    ),
    "s_n_money_out": Phrase(
        "SENDER_HISTORY",
        "count",
        "the sender has made {v} [transfer or cash-out|transfers or cash-outs] before",
        "প্রেরক আগে {v}টি টাকা পাঠানো বা ক্যাশ-আউট করেছেন",
        "this is the sender's first transfer or cash-out",
        "এটি প্রেরকের প্রথম টাকা পাঠানো বা ক্যাশ-আউট",
    ),
    "s_cnt_1h": Phrase(
        "SENDER_VELOCITY",
        "count",
        "{v} outgoing [transaction|transactions] in the last hour",
        "গত এক ঘণ্টায় {v}টি টাকা বের হওয়ার লেনদেন",
        "no other outgoing transaction in the last hour",
        "গত এক ঘণ্টায় টাকা বের হওয়ার আর কোনো লেনদেন নেই",
    ),
    "s_cnt_24h": Phrase(
        "SENDER_VELOCITY",
        "count",
        "{v} outgoing [transaction|transactions] in the last 24 hours",
        "গত ২৪ ঘণ্টায় {v}টি টাকা বের হওয়ার লেনদেন",
        "no other outgoing transaction in the last 24 hours",
        "গত ২৪ ঘণ্টায় টাকা বের হওয়ার আর কোনো লেনদেন নেই",
    ),
    "s_sum_24h": Phrase(
        "SENDER_VELOCITY",
        "taka",
        "{v} already sent out in the last 24 hours",
        "গত ২৪ ঘণ্টায় ইতিমধ্যে {v} বের হয়েছে",
        "nothing else sent out in the last 24 hours",
        "গত ২৪ ঘণ্টায় আর কোনো টাকা বের হয়নি",
    ),
    "s_new_recipients_24h": Phrase(
        "SENDER_VELOCITY",
        "count",
        "{v} first-time [recipient|recipients] in the last 24 hours",
        "গত ২৪ ঘণ্টায় {v} জন নতুন প্রাপক",
        "no first-time recipient in the last 24 hours",
        "গত ২৪ ঘণ্টায় কোনো নতুন প্রাপক নেই",
    ),
    "s_secs_since_last_txn": Phrase(
        "SENDER_VELOCITY",
        "secs",
        "{v} after the sender's previous transaction",
        "প্রেরকের আগের লেনদেনের {v} পর",
    ),
    "s_secs_since_funding": Phrase(
        "SENDER_FUNDING",
        "secs",
        "{v} after the wallet was last topped up",
        "ওয়ালেটে সর্বশেষ টাকা ভরার {v} পর",
    ),
    "s_funded_share_1h": Phrase(
        "SENDER_FUNDING",
        "pct",
        "a top-up in the last hour covers {v} of this amount",
        "গত এক ঘণ্টার টপ-আপ এই পরিমাণের {v}",
        "no top-up in the last hour",
        "গত এক ঘণ্টায় কোনো টপ-আপ হয়নি",
    ),
    "s_new_device": Phrase(
        "SENDER_DEVICE",
        "flag",
        "first transaction from a handset this wallet has never used",
        "এই ওয়ালেটে আগে কখনো ব্যবহার হয়নি এমন হ্যান্ডসেট থেকে প্রথম লেনদেন",
        "not a first-time handset",
        "হ্যান্ডসেটটি প্রথমবার ব্যবহৃত নয়",
    ),
    "s_device_age_secs": Phrase(
        "SENDER_DEVICE",
        "secs",
        "the handset was first used on this wallet {v} ago",
        "হ্যান্ডসেটটি এই ওয়ালেটে প্রথম ব্যবহার হয়েছে {v} আগে",
        "the handset has not been used on this wallet before",
        "হ্যান্ডসেটটি এই ওয়ালেটে আগে ব্যবহার হয়নি",
    ),
    "s_device_other_wallets": Phrase(
        "SENDER_DEVICE",
        "count",
        "{v} other [wallet has|wallets have] used the same handset",
        "একই হ্যান্ডসেট আরও {v}টি ওয়ালেট ব্যবহার করেছে",
        "no other wallet has used this handset",
        "এই হ্যান্ডসেট অন্য কোনো ওয়ালেট ব্যবহার করেনি",
    ),
    "s_device_flagged": Phrase(
        "SENDER_DEVICE",
        "flag",
        "the handset has been used by a wallet confirmed as fraud",
        "হ্যান্ডসেটটি প্রতারণা হিসেবে নিশ্চিত একটি ওয়ালেট ব্যবহার করেছে",
        "the handset has no link to confirmed fraud",
        "হ্যান্ডসেটটির সাথে নিশ্চিত প্রতারণার কোনো সম্পর্ক নেই",
    ),
    "s_fan_in_7d": Phrase(
        "SENDER_PASS_THROUGH",
        "count",
        "the sender received money from {v} different [wallet|wallets] in the last 7 days",
        "প্রেরক গত ৭ দিনে {v}টি ভিন্ন ওয়ালেট থেকে টাকা পেয়েছেন",
        "the sender received no transfers in the last 7 days",
        "প্রেরক গত ৭ দিনে কোনো টাকা পাননি",
    ),
    "s_in_sum_24h": Phrase(
        "SENDER_PASS_THROUGH",
        "taka",
        "the sender received {v} in the last 24 hours",
        "প্রেরক গত ২৪ ঘণ্টায় {v} পেয়েছেন",
        "the sender received nothing in the last 24 hours",
        "প্রেরক গত ২৪ ঘণ্টায় কোনো টাকা পাননি",
    ),
    "s_secs_since_last_in": Phrase(
        "SENDER_PASS_THROUGH",
        "secs",
        "{v} after the sender last received money",
        "প্রেরক সর্বশেষ টাকা পাওয়ার {v} পর",
    ),
    "s_out_in_ratio_24h": Phrase(
        "SENDER_PASS_THROUGH",
        "times",
        "money out over the last 24 hours is {v} the money received",
        "গত ২৪ ঘণ্টায় বের হওয়া টাকা প্রাপ্ত টাকার {v}",
    ),
    "s_flagged_neighbors": Phrase(
        "SENDER_FRAUD_LINK",
        "count",
        "the sender has transacted with {v} [wallet|wallets] confirmed as fraud",
        "প্রেরক প্রতারণা হিসেবে নিশ্চিত {v}টি ওয়ালেটের সাথে লেনদেন করেছেন",
        "the sender has not transacted with any wallet confirmed as fraud",
        "প্রেরক নিশ্চিত প্রতারণার কোনো ওয়ালেটের সাথে লেনদেন করেননি",
    ),
    "s_flagged_hops": Phrase(
        "SENDER_FRAUD_LINK",
        "count",
        "the sender is {v} [transfer|transfers] away from a wallet confirmed as fraud",
        "প্রেরক প্রতারণা হিসেবে নিশ্চিত একটি ওয়ালেট থেকে {v} ধাপ দূরে",
        "the sender's own wallet is confirmed as fraud",
        "প্রেরকের নিজের ওয়ালেট প্রতারণা হিসেবে নিশ্চিত",
    ),
    "pair_common_contacts": Phrase(
        "RELATIONSHIP",
        "count",
        "sender and receiver have {v} [contact|contacts] in common",
        "প্রেরক ও প্রাপকের {v} জন অভিন্ন পরিচিত আছে",
        "sender and receiver have no contacts in common",
        "প্রেরক ও প্রাপকের কোনো অভিন্ন পরিচিত নেই",
    ),
    "pair_prior_count": Phrase(
        "RELATIONSHIP",
        "count",
        "the sender has paid this receiver {v} [time|times] before",
        "প্রেরক এই প্রাপককে আগে {v} বার টাকা দিয়েছেন",
        "the sender has never paid this receiver before",
        "প্রেরক এই প্রাপককে আগে কখনো টাকা দেননি",
    ),
    "pair_reverse_count": Phrase(
        "RELATIONSHIP",
        "count",
        "the receiver has paid the sender {v} [time|times] before",
        "প্রাপক আগে প্রেরককে {v} বার টাকা দিয়েছেন",
        "the receiver has never paid the sender",
        "প্রাপক কখনো প্রেরককে টাকা দেননি",
    ),
    "pair_same_district": Phrase(
        "RELATIONSHIP",
        "flag",
        "sender and receiver are from the same district",
        "প্রেরক ও প্রাপক একই জেলার",
        "sender and receiver are from different districts",
        "প্রেরক ও প্রাপক ভিন্ন জেলার",
    ),
    "r_age_days": Phrase(
        "RECIPIENT_NEW_WALLET",
        "days",
        "the receiving wallet was opened {v} ago",
        "প্রাপক ওয়ালেট {v} আগে খোলা হয়েছে",
    ),
    "r_n_initiated": Phrase(
        "RECIPIENT_NEW_WALLET",
        "count",
        "it has started {v} [transaction|transactions] of its own",
        "এটি নিজে {v}টি লেনদেন করেছে",
        "it has never started a transaction of its own",
        "এটি নিজে কখনো কোনো লেনদেন করেনি",
    ),
    "r_n_recipients": Phrase(
        "RECIPIENT_NEW_WALLET",
        "count",
        "it has paid {v} different [wallet|wallets]",
        "এটি {v}টি ভিন্ন ওয়ালেটে টাকা পাঠিয়েছে",
        "it has never paid another wallet",
        "এটি কখনো অন্য ওয়ালেটে টাকা পাঠায়নি",
    ),
    "r_fan_in_24h": Phrase(
        "RECIPIENT_INFLOW",
        "count",
        "it received from {v} different [wallet|wallets] in the last 24 hours",
        "এটি গত ২৪ ঘণ্টায় {v}টি ভিন্ন ওয়ালেট থেকে টাকা পেয়েছে",
        "it received nothing in the last 24 hours",
        "এটি গত ২৪ ঘণ্টায় কোনো টাকা পায়নি",
    ),
    "r_fan_in_7d": Phrase(
        "RECIPIENT_INFLOW",
        "count",
        "it received from {v} different [wallet|wallets] in the last 7 days",
        "এটি গত ৭ দিনে {v}টি ভিন্ন ওয়ালেট থেকে টাকা পেয়েছে",
        "it received nothing in the last 7 days",
        "এটি গত ৭ দিনে কোনো টাকা পায়নি",
    ),
    "r_in_cnt_24h": Phrase(
        "RECIPIENT_INFLOW",
        "count",
        "{v} incoming [transfer|transfers] in the last 24 hours",
        "গত ২৪ ঘণ্টায় {v}টি টাকা এসেছে",
        "no incoming transfer in the last 24 hours",
        "গত ২৪ ঘণ্টায় কোনো টাকা আসেনি",
    ),
    "r_in_sum_24h": Phrase(
        "RECIPIENT_INFLOW",
        "taka",
        "{v} received in the last 24 hours",
        "গত ২৪ ঘণ্টায় {v} এসেছে",
        "nothing received in the last 24 hours",
        "গত ২৪ ঘণ্টায় কোনো টাকা আসেনি",
    ),
    "r_in_cnt_7d": Phrase(
        "RECIPIENT_INFLOW",
        "count",
        "{v} incoming [transfer|transfers] in the last 7 days",
        "গত ৭ দিনে {v}টি টাকা এসেছে",
        "no incoming transfer in the last 7 days",
        "গত ৭ দিনে কোনো টাকা আসেনি",
    ),
    "r_in_per_day": Phrase(
        "RECIPIENT_INFLOW",
        "rate",
        "it receives {v} transfers per day on average",
        "এটি গড়ে প্রতিদিন {v}টি টাকা পায়",
    ),
    "r_new_sender_share_7d": Phrase(
        "RECIPIENT_INFLOW",
        "pct",
        "{v} of its incoming transfers this week came from first-time senders",
        "এই সপ্তাহে আসা টাকার {v} প্রথমবারের প্রেরকদের কাছ থেকে",
    ),
    "r_cross_district_share_7d": Phrase(
        "RECIPIENT_INFLOW",
        "pct",
        "{v} of its incoming transfers this week came from other districts",
        "এই সপ্তাহে আসা টাকার {v} অন্য জেলা থেকে",
    ),
    "r_sender_districts_7d": Phrase(
        "RECIPIENT_INFLOW",
        "count",
        "its senders this week are from {v} [district|districts]",
        "এই সপ্তাহে এর প্রেরকরা {v}টি জেলার",
    ),
    "r_reciprocity": Phrase(
        "RECIPIENT_INFLOW",
        "pct",
        "it has paid back {v} of the wallets that sent it money",
        "যারা টাকা পাঠিয়েছে তাদের {v}-কে এটি কখনো টাকা পাঠিয়েছে",
    ),
    "r_dwell_secs": Phrase(
        "RECIPIENT_PASS_THROUGH",
        "secs",
        "money typically leaves it {v} after arriving",
        "টাকা আসার সাধারণত {v} পর এখান থেকে বের হয়ে যায়",
    ),
    "r_fast_exit_share": Phrase(
        "RECIPIENT_PASS_THROUGH",
        "pct",
        "{v} of the transfers it received were followed by money leaving within 30 minutes",
        "প্রাপ্ত টাকার {v} ক্ষেত্রে ৩০ মিনিটের মধ্যে টাকা বের হয়ে গেছে",
        "it has never moved money out within 30 minutes of receiving it",
        "টাকা পাওয়ার ৩০ মিনিটের মধ্যে এটি কখনো টাকা বের করেনি",
    ),
    "r_out_in_ratio_24h": Phrase(
        "RECIPIENT_PASS_THROUGH",
        "times",
        "in the last 24 hours it moved out {v} what it received",
        "গত ২৪ ঘণ্টায় এটি প্রাপ্ত টাকার {v} বের করেছে",
    ),
    "r_cashout_share": Phrase(
        "RECIPIENT_PASS_THROUGH",
        "pct",
        "its cash-outs amount to {v} of the money it has received by transfer",
        "এর ক্যাশ-আউট এ পর্যন্ত ট্রান্সফারে পাওয়া টাকার {v}",
        "it has never cashed out",
        "এটি কখনো ক্যাশ-আউট করেনি",
    ),
    "r_device_other_wallets": Phrase(
        "RECIPIENT_FRAUD_LINK",
        "count",
        "{v} other [wallet shares|wallets share] the receiver's handset",
        "প্রাপকের হ্যান্ডসেট আরও {v}টি ওয়ালেট ব্যবহার করে",
        "no other wallet shares the receiver's handset",
        "প্রাপকের হ্যান্ডসেট অন্য কোনো ওয়ালেট ব্যবহার করে না",
    ),
    "r_device_flagged": Phrase(
        "RECIPIENT_FRAUD_LINK",
        "flag",
        "the receiver's handset has been used by a wallet confirmed as fraud",
        "প্রাপকের হ্যান্ডসেট প্রতারণা হিসেবে নিশ্চিত একটি ওয়ালেট ব্যবহার করেছে",
        "the receiver's handset has no link to confirmed fraud",
        "প্রাপকের হ্যান্ডসেটের সাথে নিশ্চিত প্রতারণার কোনো সম্পর্ক নেই",
    ),
    "r_flagged_neighbors": Phrase(
        "RECIPIENT_FRAUD_LINK",
        "count",
        "the receiver has transacted with {v} [wallet|wallets] confirmed as fraud",
        "প্রাপক প্রতারণা হিসেবে নিশ্চিত {v}টি ওয়ালেটের সাথে লেনদেন করেছে",
        "the receiver has not transacted with any wallet confirmed as fraud",
        "প্রাপক নিশ্চিত প্রতারণার কোনো ওয়ালেটের সাথে লেনদেন করেনি",
    ),
    "r_flagged_hops": Phrase(
        "RECIPIENT_FRAUD_LINK",
        "count",
        "the receiver is {v} [transfer|transfers] away from a wallet confirmed as fraud",
        "প্রাপক প্রতারণা হিসেবে নিশ্চিত একটি ওয়ালেট থেকে {v} ধাপ দূরে",
        "the receiver's own wallet is confirmed as fraud",
        "প্রাপকের নিজের ওয়ালেট প্রতারণা হিসেবে নিশ্চিত",
    ),
    "a_n_cashouts": Phrase(
        "AGENT_PATTERN",
        "count",
        "the agent has handled {v} [cash-out|cash-outs]",
        "এজেন্ট {v}টি ক্যাশ-আউট সম্পন্ন করেছে",
        "the agent has no cash-out history",
        "এজেন্টের কোনো ক্যাশ-আউট ইতিহাস নেই",
    ),
    "a_young_share": Phrase(
        "AGENT_PATTERN",
        "pct",
        "{v} of the agent's cash-outs are from wallets under 30 days old",
        "এজেন্টের ক্যাশ-আউটের {v} ৩০ দিনের কম বয়সী ওয়ালেট থেকে",
    ),
    "a_out_district_share": Phrase(
        "AGENT_PATTERN",
        "pct",
        "{v} of the agent's cash-outs are by customers from other districts",
        "এজেন্টের ক্যাশ-আউটের {v} অন্য জেলার গ্রাহকদের",
    ),
    "a_fast_exit_share": Phrase(
        "AGENT_PATTERN",
        "pct",
        "{v} of the agent's cash-outs happen within 30 minutes of the customer receiving money",
        "এজেন্টের ক্যাশ-আউটের {v} গ্রাহক টাকা পাওয়ার ৩০ মিনিটের মধ্যে হয়",
    ),
    "a_night_share": Phrase(
        "AGENT_PATTERN",
        "pct",
        "{v} of the agent's cash-outs happen between midnight and 6am",
        "এজেন্টের ক্যাশ-আউটের {v} রাত ১২টা থেকে ভোর ৬টার মধ্যে হয়",
    ),
    "a_flagged_customers": Phrase(
        "AGENT_PATTERN",
        "count",
        "{v} of the agent's customers [was|were] confirmed as fraud",
        "এজেন্টের {v} জন গ্রাহক প্রতারণা হিসেবে নিশ্চিত হয়েছে",
        "none of the agent's customers has been confirmed as fraud",
        "এজেন্টের কোনো গ্রাহক প্রতারণা হিসেবে নিশ্চিত হয়নি",
    ),
}

# Topic titles. A title names what was looked at; the facts say what was found.
GROUPS: dict[str, tuple[str, str]] = {
    "RECIPIENT_PASS_THROUGH": (
        "How fast the receiving wallet moves money on",
        "প্রাপক ওয়ালেট কত দ্রুত টাকা সরিয়ে ফেলে",
    ),
    "RECIPIENT_NEW_WALLET": (
        "Age and own activity of the receiving wallet",
        "প্রাপক ওয়ালেটের বয়স ও নিজস্ব লেনদেন",
    ),
    "RECIPIENT_INFLOW": ("Who sends money to the receiving wallet", "প্রাপক ওয়ালেটে কারা টাকা পাঠায়"),
    "RECIPIENT_FRAUD_LINK": (
        "Links between the receiver and confirmed fraud",
        "প্রাপকের সাথে নিশ্চিত প্রতারণার সম্পর্ক",
    ),
    "SENDER_DEVICE": ("The handset used for this transaction", "এই লেনদেনে ব্যবহৃত হ্যান্ডসেট"),
    "AMOUNT": ("The amount compared with the sender's habits", "প্রেরকের অভ্যাসের তুলনায় পরিমাণ"),
    "SENDER_VELOCITY": ("The sender's recent activity", "প্রেরকের সাম্প্রতিক লেনদেন"),
    "SENDER_FUNDING": ("Where the money came from", "টাকা কোথা থেকে এসেছে"),
    "SENDER_PASS_THROUGH": (
        "Money passing through the sender's wallet",
        "প্রেরকের ওয়ালেট দিয়ে টাকা পার হওয়া",
    ),
    "SENDER_FRAUD_LINK": (
        "Links between the sender and confirmed fraud",
        "প্রেরকের সাথে নিশ্চিত প্রতারণার সম্পর্ক",
    ),
    "SENDER_HISTORY": ("The sender's history", "প্রেরকের ইতিহাস"),
    "TIME_AND_PLACE": ("Time and place of the transaction", "লেনদেনের সময় ও স্থান"),
    "RELATIONSHIP": ("History between sender and receiver", "প্রেরক ও প্রাপকের পূর্ব সম্পর্ক"),
    "AGENT_PATTERN": ("The cash-out agent's pattern", "ক্যাশ-আউট এজেন্টের ধরন"),
}

# Reason codes that only a policy rule can raise.
RULE_CODES = {
    "RECIPIENT_CONFIRMED_FRAUD",
    "SENDER_CONFIRMED_FRAUD",
    "RECIPIENT_MULE_SCORE",
    "SMALL_AMOUNT",
    "UNUSUAL_PLACE",
    "UNFAMILIAR_NETWORK",
    *GROUPS,
}

_NUMBER_FORM = re.compile(r"\[([^|\]]*)\|([^\]]*)\]")
_UNEXPLAINED = ("is_cash_out",)  # transaction type: kept in the score, not shown as a reason
assert set(PHRASES) | set(_UNEXPLAINED) == set(FEATURES), "every feature needs a phrase"
assert {p.group for p in PHRASES.values()} == set(GROUPS)


def fact(feature: str, value: float) -> dict | None:
    """One feature as a sentence in both languages, or None when the value is missing."""
    phrase = PHRASES.get(feature)
    if phrase is None or value is None or math.isnan(value):
        return None
    if value == 0 and phrase.en_zero:
        en, bn = phrase.en_zero, phrase.bn_zero
    elif phrase.kind == "flag":
        en, bn = phrase.en, phrase.bn
    else:
        v_en, v_bn = render(phrase.kind, value)
        # "[wallet|wallets]" in an English phrase picks the form that fits the value.
        en = _NUMBER_FORM.sub(r"\1" if value == 1 else r"\2", phrase.en).format(v=v_en)
        bn = phrase.bn.format(v=v_bn)
    return {"feature": feature, "value": round(float(value), 4), "en": en, "bn": bn}


def explain(
    values: Sequence[float],
    contributions: Sequence[float],
    max_raising: int = 4,
    max_lowering: int = 2,
    facts_per_reason: int = 3,
    min_share: float = 0.05,
) -> list[dict]:
    """Reasons for one transaction from its feature values and SHAP contributions.

    Contributions are in log-odds, in FEATURES order. Returns the topics that push
    the score up (largest first), then the ones that push it down. `share` is the
    topic's part of all upward (or downward) push.
    """
    by_group: dict[str, list[tuple[float, str, float]]] = {}
    for name, value, c in zip(FEATURES, values, contributions, strict=True):
        phrase = PHRASES.get(name)
        if phrase is not None:
            by_group.setdefault(phrase.group, []).append((float(c), name, float(value)))
    totals = {g: sum(c for c, _, _ in items) for g, items in by_group.items()}
    up = sum(t for t in totals.values() if t > 0) or 1.0
    down = -sum(t for t in totals.values() if t < 0) or 1.0

    reasons: list[dict] = []
    for direction, sign, limit, denom in (
        ("raises", 1.0, max_raising, up),
        ("lowers", -1.0, max_lowering, down),
    ):
        ranked = sorted(totals.items(), key=lambda kv: -sign * kv[1])
        for group, total in ranked[:limit]:
            share = sign * total / denom
            if share < min_share:
                break
            members = sorted(by_group[group], key=lambda m: -sign * m[0])
            facts = [
                f
                for c, name, value in members
                if sign * c > 0 and (f := fact(name, value)) is not None
            ][:facts_per_reason]
            if not facts:
                continue
            title_en, title_bn = GROUPS[group]
            reasons.append(
                {
                    "code": group,
                    "source": "model",
                    "direction": direction,
                    "weight": round(total, 3),
                    "share": round(share, 3),
                    "title_en": title_en,
                    "title_bn": title_bn,
                    "detail_en": "; ".join(f["en"] for f in facts),
                    "detail_bn": "; ".join(f["bn"] for f in facts),
                    "facts": facts,
                }
            )
    return reasons
