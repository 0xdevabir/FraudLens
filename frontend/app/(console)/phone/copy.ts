// What the demo phone says, in the customer's language. The Bangla is written for
// the screen, the way upay's app speaks to its customers, not translated word by word.

import type { AppealRelation, Cue, Tier } from "@/lib/types";

export type Lang = "bn" | "en";
export type Words = Record<Lang, string>;

const BN_DIGITS = "০১২৩৪৫৬৭৮৯";

/** Bangla numerals for a Bangla screen; amounts, times and counts all read naturally. */
export function digits(text: string | number, lang: Lang): string {
  const value = String(text);
  return lang === "bn" ? value.replace(/[0-9]/g, (d) => BN_DIGITS[Number(d)]) : value;
}

/** mm:ss, or h:mm:ss past an hour. */
export function clockText(seconds: number, lang: Lang): string {
  const s = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return digits(h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`, lang);
}

/** The spoken form for screen readers: "12 minutes 5 seconds". */
export function clockSpoken(seconds: number, lang: Lang): string {
  const s = Math.max(0, Math.ceil(seconds));
  const m = Math.floor(s / 60);
  return lang === "bn" ? `${digits(m, lang)} মিনিট ${digits(s % 60, lang)} সেকেন্ড` : `${m} minutes ${s % 60} seconds`;
}

/** The warning itself, by the kind of scam the decision looks like (the API's `cue`).
 * Short words a busy customer can read in a glance — not policy jargon. */
export const CUE: Record<Cue, { title: Words; body: Words; check: Words }> = {
  reported_recipient: {
    title: { bn: "এই নম্বরে অভিযোগ আছে", en: "People reported this number" },
    body: {
      bn: "অনেকে এখানে টাকা পাঠিয়ে ঠকেছেন। চেনেন না, বা কেউ তাড়া দিচ্ছে — পাঠাবেন না।",
      en: "Others lost money sending here. Don't send if you don't know them, or if someone is rushing you.",
    },
    check: { bn: "নিজে ফোন করে জিজ্ঞাসা করুন।", en: "Call them yourself first." },
  },
  impersonation: {
    title: { bn: "কেউ কি অফিসের লোক সেজেছে?", en: "Did someone pretend to be staff?" },
    body: {
      bn: "upay কখনো পিন, ওটিপি বা টাকা চায় না। কেউ চাইলে ফোন কেটে দিন।",
      en: "upay never asks for your PIN, OTP or money. Hang up if they do.",
    },
    check: { bn: "সন্দেহ হলে ১৬২৬৮-এ কল করুন।", en: "If unsure, call 16268." },
  },
  prize: {
    title: { bn: "পুরস্কার পেতে টাকা লাগে না", en: "Prizes never need a fee" },
    body: {
      bn: "লটারি বা উপহারের আগে ফি পাঠাতে বললে সেটা প্রতারণা।",
      en: "If they ask for a fee before a prize or gift, it's a scam.",
    },
    check: { bn: "আপনি কি সত্যি কোনো প্রতিযোগিতায় ছিলেন?", en: "Did you really enter a contest?" },
  },
  investment: {
    title: { bn: "দ্রুত লাভ? সাবধান", en: "Quick profit? Be careful" },
    body: {
      bn: "টাকা দ্বিগুণ বা নিশ্চিত লাভের কথা বলে নেওয়া — পরিচিত প্রতারণা।",
      en: "Promises to double your money or guarantee profit are a common scam.",
    },
    check: { bn: "আগে পরিবারের কারো সাথে কথা বলুন।", en: "Talk to family before you send." },
  },
  wrong_send: {
    title: { bn: "'ভুল পাঠিয়েছি' বলে টাকা চাইছে?", en: "Asking you to return a 'wrong send'?" },
    body: {
      bn: "আগে অ্যাপে ব্যালেন্স আর লেনদেন দেখুন। টাকা না এলে কিছু ফেরত পাঠাবেন না।",
      en: "Check your balance and history in the app first. Don't send anything back until the money is there.",
    },
    check: { bn: "এসএমএস নয় — অ্যাপের তালিকা দেখুন।", en: "Trust the app history, not an SMS." },
  },
  not_you: {
    title: { bn: "এটা কি আপনিই করছেন?", en: "Is this really you?" },
    body: {
      bn: "নতুন ফোন, নতুন জায়গা বা অস্বাভাবিক অঙ্ক — আপনার স্বাভাবিক লেনদেনের মতো নয়।",
      en: "New phone, new place or an odd amount — not how you usually pay.",
    },
    check: { bn: "আপনি না হলে বাতিল করে ১৬২৬৮-এ কল করুন।", en: "If it isn't you, cancel and call 16268." },
  },
  generic: {
    title: { bn: "পাঠানোর আগে একটু থামুন", en: "Stop a second before you send" },
    body: {
      bn: "এই টাকা পাঠানো প্রতারণার মতো দেখাচ্ছে।",
      en: "This payment looks like a scam.",
    },
    check: { bn: "প্রাপককে চেনেন? কেউ তাড়া দিচ্ছে?", en: "Do you know them? Is anyone rushing you?" },
  },
};

/** The strip under the bar: what kind of interruption this is. */
export const STRIP_TEXT: Record<Exclude<Tier, "allow">, Words> = {
  warn: { bn: "থামুন — ঝুঁকি আছে", en: "Stop — this looks risky" },
  step_up: { bn: "আবার চেক করুন", en: "Check again" },
  hold: { bn: "টাকা আটকে আছে", en: "Money on hold" },
};

export const RELATIONS: [AppealRelation, Words][] = [
  ["family", { bn: "পরিবারের সদস্য", en: "Family" }],
  ["friend", { bn: "বন্ধু", en: "Friend" }],
  ["seller", { bn: "দোকান বা বিক্রেতা", en: "A shop or seller" }],
  ["business", { bn: "ব্যবসার কাজে", en: "Business" }],
  ["landlord", { bn: "বাড়িওয়ালা", en: "Landlord" }],
  ["employer", { bn: "অফিস বা মালিক", en: "Employer" }],
  ["other", { bn: "অন্য কেউ", en: "Someone else" }],
  ["none", { bn: "চিনি না", en: "I don't know them" }],
];

/** Everything else on the phone, keyed by what it is for. */
export const T = {
  appName: { bn: "সেন্ড মানি", en: "Send money" },
  switchTo: { bn: "English", en: "বাংলা" },
  switchLabel: { bn: "Switch the app to English", en: "অ্যাপটি বাংলায় দেখুন" },
  back: { bn: "পেছনে যান", en: "Back" },
  to: { bn: "প্রাপক", en: "To" },
  amount: { bn: "পরিমাণ", en: "Amount" },
  balance: { bn: "বর্তমান ব্যালেন্স", en: "Balance" },
  from: { bn: "যে অ্যাকাউন্ট থেকে", en: "From" },
  chooseLeft: { bn: "শুরু করতে বাঁ দিক থেকে একটি লেনদেন বেছে নিন।", en: "Choose a payment on the left to begin." },
  tapSend: { bn: "সেন্ড মানি চাপুন", en: "Tap Send money" },
  demoOnly: { bn: "ডেমোতে শুধু সেন্ড মানি কাজ করে", en: "In this demo, only Send money works" },
  helpline: { bn: "হেল্পলাইন ১৬২৬৮", en: "Helpline 16268" },
  beforeYouSend: { bn: "পাঠানোর আগে", en: "Before you send" },
  send: { bn: "সেন্ড মানি করুন", en: "Send" },
  sending: { bn: "পাঠানো হচ্ছে…", en: "Sending…" },
  cancel: { bn: "বাতিল করুন", en: "Cancel the payment" },
  sendAnyway: { bn: "তবুও পাঠাব", en: "Send anyway" },
  sendConfirm: { bn: "নিশ্চিত? টাকা ফেরত আনা কঠিন", en: "Sure? Money is hard to get back" },
  sendConfirmYes: { bn: "হ্যাঁ, পাঠান", en: "Yes, send it" },
  neverMind: { bn: "না, ফিরে যাই", en: "No, go back" },
  pin: { bn: "পিন দিন", en: "Enter your PIN" },
  pinClear: { bn: "মুছুন", en: "Clear" },
  verifySend: { bn: "যাচাই করে পাঠান", en: "Verify and send" },
  availableIn: { bn: "পাঠানো যাবে আর", en: "You can send in" },
  coolingOff: {
    bn: "এই অপেক্ষা আপনার সুরক্ষার জন্য। কে টাকা চাইছে ভেবে দেখুন। যেকোনো সময় বাতিল করতে পারেন।",
    en: "This wait is for your safety. Think about who asked you to pay. You can cancel anytime.",
  },
  waitOver: { bn: "অপেক্ষা শেষ। পিন দিয়ে পাঠান।", en: "Wait over. Enter your PIN to send." },
  heldMoney: { bn: "টাকা এখনো আপনার অ্যাকাউন্টেই আছে।", en: "Your money is still in your account." },
  reviewIn: { bn: "কর্মকর্তা দেখবেন, বাকি", en: "Someone will check within" },
  reviewLate: {
    bn: "চেক করতে একটু বেশি লাগছে। দরকার হলে ১৬২৬৮-এ কল করুন।",
    en: "The check is taking longer. Call 16268 if you need help.",
  },
  whatNext: { bn: "এরপর কী হবে", en: "What happens next" },
  tipTap: { bn: "টিপস — চাপলে টিক হবে", en: "Tips — tap to check off" },
  tipKnow: { bn: "প্রাপককে চিনি", en: "I know who this is" },
  tipRush: { bn: "কেউ তাড়া দিচ্ছে না", en: "Nobody is rushing me" },
  report: { bn: "প্রতারণা জানান", en: "Report a scam" },
  whatHappened: { bn: "কী হয়েছিল?", en: "What happened?" },
  reportDone: { bn: "অভিযোগ নেওয়া হয়েছে।", en: "Report received." },
  appeal: { bn: "এটা সঠিক — আপিল করুন", en: "This is fine — appeal" },
  appealTitle: { bn: "আপিল করুন", en: "Appeal" },
  appealIntro: {
    bn: "লেনদেন ঠিক মনে হলে জানান। একজন কর্মকর্তা দেখে সিদ্ধান্ত নেবেন।",
    en: "If this payment looks fine, tell us. A person will decide.",
  },
  relation: { bn: "প্রাপক আপনার কে?", en: "Who is this person to you?" },
  reason: { bn: "কেন পাঠাচ্ছেন?", en: "Why are you sending?" },
  reasonHint: { bn: "পিন বা ওটিপি এখানে লিখবেন না।", en: "Don't write your PIN or OTP here." },
  submitAppeal: { bn: "আপিল জমা দিন", en: "Send the appeal" },
  appealSent: { bn: "আপিল নেওয়া হয়েছে", en: "Appeal received" },
  appealBy: { bn: "উত্তর পাবেন", en: "Answer by" },
  appealNoMoney: {
    bn: "আপিলে অপেক্ষা কমবে না — শুধু আমাদের সতর্কতা ভালো করতে সাহায্য করে।",
    en: "An appeal won't shorten the wait — it helps us improve warnings.",
  },
  appealApproved: { bn: "আপিল মঞ্জুর", en: "Appeal accepted" },
  appealRejected: { bn: "আপিল মঞ্জুর হয়নি — চেক চলছে", en: "Appeal not accepted — review continues" },
  checkStatus: { bn: "অবস্থা দেখুন", en: "Check status" },
  sent: { bn: "টাকা পাঠানো হয়েছে", en: "Sent" },
  cancelled: { bn: "বাতিল হয়েছে", en: "Cancelled" },
  notSent: { bn: "পাঠানো হয়নি", en: "Not sent" },
  sentBody: { bn: "টাকা চলে গেছে।", en: "The money went through." },
  cancelledBody: { bn: "কিছু যায়নি। টাকা আপনার কাছেই আছে।", en: "Nothing left. Your money is still yours." },
  refusedBody: { bn: "লেনদেন হয়নি। টাকা আপনার কাছেই আছে।", en: "Payment refused. Your money is still yours." },
  newPayment: { bn: "নতুন লেনদেন", en: "New payment" },
  reportForRefund: { bn: "ঠকেছেন? জানিয়ে টাকা ফেরত চান", en: "Scammed? Report to get money back" },
  refundTitle: { bn: "টাকা ফেরত", en: "Your refund" },
  refundBy: { bn: "খবর পাবেন", en: "We'll tell you by" },
  refundNoFee: {
    bn: "ফেরত পেতে পিন, ওটিপি বা ফি দিতে হয় না। কেউ চাইলে সে প্রতারক।",
    en: "Never give a PIN, OTP or fee for a refund. Anyone who asks is a scammer.",
  },
  refundPaid: { bn: "টাকা আপনার অ্যাকাউন্টে ফেরত এসেছে", en: "returned to your account" },
  refundPartOf: { bn: "হারানো", en: "of the" },
  refundPartTail: {
    bn: "টাকার মধ্যে যা বাকি ছিল, তা ফেরত দেওয়া হয়েছে।",
    en: "you lost came back from what was left.",
  },
  refundNothingLeft: {
    bn: "প্রতারণা নিশ্চিত, কিন্তু টাকা আগেই তুলে নেওয়া। সাহায্যে ১৬২৬৮।",
    en: "Scam confirmed, but the money was already gone. Call 16268.",
  },
  refundDeclined: {
    bn: "প্রতারণা নিশ্চিত হয়নি, তাই ফেরত নেই। একমত না হলে ১৬২৬৮।",
    en: "We couldn't confirm a scam, so no refund. Call 16268 if you disagree.",
  },
  checkRefund: { bn: "ফেরতের অবস্থা দেখুন", en: "Check my refund" },
  minutes: { bn: "মিনিট", en: "minutes" },
  homeTab: { bn: "হোম", en: "Home" },
  historyTab: { bn: "ইতিহাস", en: "History" },
  moreTab: { bn: "আরও", en: "More" },
} satisfies Record<string, Words>;

/** The road to a refund: reported, the receiver frozen, the scam confirmed, money back. */
export function refundSteps(frozen: boolean, settled: boolean, lang: Lang): { text: string; state: "done" | "now" | "next" }[] {
  const words: [Words, boolean][] = [
    [{ bn: "অভিযোগ গ্রহণ করা হয়েছে", en: "We've received your report" }, true],
    [
      frozen
        ? { bn: "প্রাপকের অ্যাকাউন্ট আটকানো হয়েছে — সে আর টাকা তুলতে পারবে না", en: "The receiver's account is frozen: they can't cash out" }
        : { bn: "প্রাপকের অ্যাকাউন্ট আটকানো হচ্ছে, যাতে সে টাকা তুলতে না পারে", en: "We're freezing the receiver's account so they can't cash out" },
      frozen,
    ],
    [{ bn: "একজন কর্মকর্তা প্রতারণা নিশ্চিত করবেন", en: "A person confirms the scam" }, settled],
    [{ bn: "তাদের অ্যাকাউন্টে যা আছে, তা থেকে আপনার টাকা ফেরত", en: "Your money comes back from what's left in their account" }, settled],
  ];
  const now = words.findIndex(([, done]) => !done);
  return words.map(([step, done], index) => ({ text: step[lang], state: done ? "done" : index === now ? "now" : "next" }));
}

/** The steps after each kind of interruption: done, happening now, still to come. */
export function steps(tier: Exclude<Tier, "allow">, minutes: number | null, lang: Lang): { text: string; state: "done" | "now" | "next" }[] {
  const n = digits(minutes ?? 30, lang);
  const words = {
    warn: [
      { bn: "আমরা সতর্ক করেছি", en: "We've warned you" },
      { bn: "আপনি ঠিক করুন: বাতিল বা তবুও পাঠান", en: "You choose: cancel or send anyway" },
      { bn: "পাঠালে টাকা চলে যায় — ফেরত কঠিন", en: "If you send, money leaves at once — hard to get back" },
    ],
    step_up: [
      { bn: "লেনদেন থামানো হয়েছে", en: "Payment paused" },
      { bn: `${n} মিনিট অপেক্ষা — যেকোনো সময় বাতিল করতে পারেন`, en: `${n}-minute wait — cancel anytime` },
      { bn: "তারপর পিন দিয়ে পাঠান", en: "Then send with your PIN" },
    ],
    hold: [
      { bn: "টাকা আপনার কাছেই আছে", en: "Money still in your account" },
      { bn: `কর্মকর্তা ${n} মিনিটের মধ্যে দেখবেন`, en: `A person checks within ${n} minutes` },
      { bn: "ঠিক হলে যাবে; না হলে আপনার কাছেই থাকবে", en: "If fine, it goes; if not, it stays with you" },
    ],
  }[tier];
  return words.map((step, index) => ({ text: step[lang], state: index === 0 ? "done" : index === 1 ? "now" : "next" }));
}




