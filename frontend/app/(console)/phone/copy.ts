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

/** The warning itself, by the kind of scam the decision looks like (the API's `cue`). */
export const CUE: Record<Cue, { title: Words; body: Words; check: Words }> = {
  reported_recipient: {
    title: { bn: "এই নম্বরের বিরুদ্ধে প্রতারণার অভিযোগ আছে", en: "This number has been reported for fraud" },
    body: {
      bn: "অন্য গ্রাহকেরা এই নম্বরে টাকা পাঠিয়ে প্রতারিত হয়েছেন। যাকে চেনেন না, বা কেউ তাড়া দিচ্ছে বলে পাঠাচ্ছেন — এমন হলে টাকা পাঠাবেন না।",
      en: "Other customers lost money sending to this number. Don't send if you don't know them, or if someone is rushing you.",
    },
    check: { bn: "প্রাপককে নিজে ফোন করে নিশ্চিত হোন।", en: "Call the person yourself to make sure it's them." },
  },
  impersonation: {
    title: { bn: "কেউ কি upay, ব্যাংক বা সরকারি অফিসের লোক পরিচয় দিয়েছে?", en: "Did someone say they're from upay, a bank or the government?" },
    body: {
      bn: "upay কখনো ফোন করে পিন, ওটিপি বা টাকা চায় না। কেউ চাইলে ফোন কেটে দিন।",
      en: "upay never calls to ask for your PIN, OTP or money. If someone does, hang up.",
    },
    check: { bn: "সন্দেহ হলে নিজে ১৬২৬৮ নম্বরে কল করুন।", en: "If in doubt, call 16268 yourself." },
  },
  prize: {
    title: { bn: "পুরস্কার পেতে টাকা লাগে না", en: "Real prizes don't cost money" },
    body: {
      bn: "লটারি, পুরস্কার বা উপহার পাওয়ার আগে ফি, ট্যাক্স বা চার্জ পাঠাতে বললে সেটা প্রতারণা।",
      en: "If you're asked to pay a fee, tax or charge before you get a prize or gift, it's a scam.",
    },
    check: { bn: "আপনি কি সত্যিই কোনো প্রতিযোগিতায় অংশ নিয়েছিলেন?", en: "Did you actually enter any contest?" },
  },
  investment: {
    title: { bn: "নিশ্চিত লাভের প্রতিশ্রুতি? সাবধান", en: "Guaranteed profit? Be careful" },
    body: {
      bn: "অল্প দিনে টাকা দ্বিগুণ বা নিশ্চিত মুনাফার কথা বলে টাকা নেওয়া একটি পরিচিত প্রতারণা।",
      en: "Promises to double your money quickly, or of guaranteed profit, are a well-known scam.",
    },
    check: { bn: "পাঠানোর আগে পরিবারের কারো সাথে কথা বলুন।", en: "Talk to someone in your family before you send." },
  },
  wrong_send: {
    title: { bn: "কেউ কি 'ভুল করে পাঠিয়েছি' বলে টাকা ফেরত চাইছে?", en: "Is someone asking for money back they 'sent by mistake'?" },
    body: {
      bn: "আগে নিজের ব্যালেন্স আর লেনদেনের তালিকা দেখুন। টাকা সত্যিই এসেছে কি না না দেখে কিছু ফেরত পাঠাবেন না।",
      en: "Check your balance and your transaction list first. Don't send anything back until you've seen the money arrive.",
    },
    check: { bn: "এসএমএস নয়, অ্যাপের লেনদেন তালিকা দেখুন — এসএমএস নকল হতে পারে।", en: "Check the app's history, not an SMS: an SMS can be faked." },
  },
  not_you: {
    title: { bn: "লেনদেনটি কি আপনি নিজেই করছেন?", en: "Is this really you?" },
    body: {
      bn: "এটি আপনার স্বাভাবিক লেনদেনের সাথে মিলছে না — নতুন ফোন, নতুন জায়গা, বা অস্বাভাবিক অঙ্ক।",
      en: "This doesn't look like how you usually pay: a new phone, a new place or an unusual amount.",
    },
    check: { bn: "আপনি না করে থাকলে বাতিল করে ১৬২৬৮ নম্বরে কল করুন।", en: "If it isn't you, cancel and call 16268." },
  },
  generic: {
    title: { bn: "পাঠানোর আগে একটু ভাবুন", en: "Take a moment before you send" },
    body: {
      bn: "এই লেনদেনটি পরিচিত প্রতারণার ধরনের সাথে মিলে যাচ্ছে।",
      en: "This payment looks like a known scam pattern.",
    },
    check: { bn: "প্রাপককে চেনেন তো? কেউ তাড়া দিচ্ছে না তো?", en: "Do you know the person? Is anyone rushing you?" },
  },
};

/** The strip under the bar: what kind of interruption this is. */
export const STRIP_TEXT: Record<Exclude<Tier, "allow">, Words> = {
  warn: { bn: "একটু থামুন", en: "Pause a moment" },
  step_up: { bn: "আপনাকে আবার যাচাই করতে হবে", en: "Verify it's you" },
  hold: { bn: "লেনদেন সাময়িকভাবে স্থগিত", en: "Payment paused for review" },
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
  helpline: { bn: "হেল্পলাইন ১৬২৬৮", en: "Helpline 16268" },
  beforeYouSend: { bn: "পাঠানোর আগে দেখুন", en: "Before you send" },
  send: { bn: "সেন্ড মানি করুন", en: "Send" },
  sending: { bn: "পাঠানো হচ্ছে…", en: "Sending…" },
  cancel: { bn: "বাতিল করুন", en: "Cancel the payment" },
  sendAnyway: { bn: "বুঝেছি, তবুও পাঠাব", en: "I understand, send anyway" },
  pin: { bn: "পিন দিন", en: "Enter your PIN" },
  verifySend: { bn: "যাচাই করে পাঠান", en: "Verify and send" },
  availableIn: { bn: "পাঠানো যাবে আর", en: "You can send in" },
  coolingOff: {
    bn: "নিরাপত্তার জন্য এই অপেক্ষা। এই সময়ে ভেবে দেখুন কে আপনাকে টাকা পাঠাতে বলেছে। চাইলে এখনই বাতিল করতে পারেন।",
    en: "This wait is for your safety. Use it to think about who asked you to pay. You can cancel at any time.",
  },
  waitOver: { bn: "অপেক্ষার সময় শেষ। পিন দিয়ে পাঠাতে পারেন।", en: "The wait is over. Enter your PIN to send." },
  heldMoney: { bn: "আপনার টাকা আপনার অ্যাকাউন্টেই আছে।", en: "Your money is still in your account." },
  reviewIn: { bn: "একজন কর্মকর্তা দেখবেন, বাকি সময়", en: "A person will review it within" },
  reviewLate: {
    bn: "পর্যালোচনায় নির্ধারিত সময়ের চেয়ে বেশি লাগছে। আমরা দুঃখিত — দরকার হলে ১৬২৬৮ নম্বরে কল করুন।",
    en: "The review is taking longer than promised. We're sorry; call 16268 if you need to.",
  },
  whatNext: { bn: "এরপর কী হবে", en: "What happens next" },
  report: { bn: "প্রতারণার অভিযোগ করুন", en: "Report a scam" },
  whatHappened: { bn: "কী হয়েছিল?", en: "What happened?" },
  reportDone: { bn: "অভিযোগ গ্রহণ করা হয়েছে।", en: "Report received." },
  appeal: { bn: "এটা আসল লেনদেন — আপিল করুন", en: "This payment is genuine: appeal" },
  appealTitle: { bn: "আপিল করুন", en: "Appeal" },
  appealIntro: {
    bn: "আপনার মনে হলে লেনদেনটি সঠিক, আমাদের জানান। একজন কর্মকর্তা দেখে সিদ্ধান্ত নেবেন।",
    en: "If you think this payment is fine, tell us. A person will look at it and decide.",
  },
  relation: { bn: "প্রাপক আপনার কে হন?", en: "How do you know the person?" },
  reason: { bn: "কেন পাঠাচ্ছেন? (সংক্ষেপে)", en: "What is the payment for? (briefly)" },
  reasonHint: { bn: "পিন বা ওটিপি এখানে লিখবেন না।", en: "Never write your PIN or OTP here." },
  submitAppeal: { bn: "আপিল জমা দিন", en: "Send the appeal" },
  appealSent: { bn: "আপিল জমা হয়েছে", en: "Appeal received" },
  appealBy: { bn: "উত্তর পাবেন যত দেরিতে হলেও", en: "You'll get an answer by" },
  appealNoMoney: {
    bn: "এই আপিলে অপেক্ষার সময় কমবে না; এটি আমাদের সতর্কবার্তা আরও ভালো করতে সাহায্য করবে।",
    en: "An appeal doesn't shorten the wait; it helps us make our warnings better.",
  },
  appealApproved: { bn: "আপিল গ্রহণ করা হয়েছে", en: "Your appeal was accepted" },
  appealRejected: { bn: "আপিল গ্রহণ করা হয়নি — কর্মকর্তা তদন্ত চালিয়ে যাচ্ছেন", en: "Your appeal wasn't accepted; the review continues" },
  checkStatus: { bn: "অবস্থা দেখুন", en: "Check the status" },
  sent: { bn: "টাকা পাঠানো হয়েছে", en: "Sent" },
  cancelled: { bn: "বাতিল হয়েছে", en: "Cancelled" },
  notSent: { bn: "লেনদেন হয়নি", en: "Not sent" },
  sentBody: { bn: "লেনদেন সফল হয়েছে।", en: "The payment went through." },
  cancelledBody: { bn: "কিছুই পাঠানো হয়নি। টাকা আপনার অ্যাকাউন্টেই আছে।", en: "Nothing was sent. Your money is still in your account." },
  refusedBody: { bn: "এই লেনদেনটি করা যায়নি। টাকা আপনার অ্যাকাউন্টেই আছে।", en: "This payment was refused. Your money is still in your account." },
  newPayment: { bn: "নতুন লেনদেন", en: "New payment" },
  minutes: { bn: "মিনিট", en: "minutes" },
} satisfies Record<string, Words>;

/** The steps after each kind of interruption: done, happening now, still to come. */
export function steps(tier: Exclude<Tier, "allow">, minutes: number | null, lang: Lang): { text: string; state: "done" | "now" | "next" }[] {
  const n = digits(minutes ?? 30, lang);
  const words = {
    warn: [
      { bn: "আমরা আপনাকে সতর্ক করেছি", en: "We've warned you" },
      { bn: "আপনি ঠিক করুন: বাতিল, নাকি তবুও পাঠাবেন", en: "You decide: cancel, or send anyway" },
      { bn: "পাঠালে টাকা সাথে সাথে চলে যাবে, আর ফেরত আনা কঠিন", en: "If you send, the money goes at once and is hard to get back" },
    ],
    step_up: [
      { bn: "আমরা লেনদেনটি থামিয়েছি", en: "We've paused the payment" },
      { bn: `${n} মিনিট অপেক্ষা — এর মধ্যে যেকোনো সময় বাতিল করা যায়`, en: `A ${n}-minute wait; you can cancel at any time` },
      { bn: "এরপর পিন দিয়ে পাঠাতে পারবেন", en: "Then you can send it with your PIN" },
    ],
    hold: [
      { bn: "টাকা আপনার অ্যাকাউন্টেই আছে, কোথাও যায়নি", en: "Your money hasn't left your account" },
      { bn: `একজন কর্মকর্তা ${n} মিনিটের মধ্যে দেখবেন`, en: `A person reviews it within ${n} minutes` },
      { bn: "ঠিক থাকলে টাকা পৌঁছে যাবে; না হলে আপনার কাছেই থাকবে", en: "If it's fine, it's delivered; if not, it stays with you" },
    ],
  }[tier];
  return words.map((step, index) => ({ text: step[lang], state: index === 0 ? "done" : index === 1 ? "now" : "next" }));
}
