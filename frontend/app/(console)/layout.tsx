import { Console } from "@/components/session";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <Console>{children}</Console>;
}
