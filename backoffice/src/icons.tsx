/**
 * Back-office icons (rule AC28, decided 8 Nov 2026): one Lucide icon per
 * place, each a different shape, always shown with its text label; one
 * size and line weight everywhere. Lucide is ISC-licensed.
 */
import {
  Activity,
  Banknote,
  Building2,
  FileSearch,
  Languages,
  LockKeyhole,
  MessageSquareText,
  Palette,
  Puzzle,
  ScanFace,
  Search,
  Wrench,
  BadgeCheck,
  Bell,
  BookOpen,
  CalendarClock,
  ChevronRight,
  ClipboardList,
  FilePen,
  Gavel,
  GraduationCap,
  History,
  IdCard,
  KeyRound,
  ListTodo,
  ScrollText,
  Globe,
  CreditCard,
  type LucideIcon,
  ShieldCheck,
  SlidersHorizontal,
  UserCog,
  Users,
  UserX,
  Wallet,
  Contact,
} from "lucide-react";

export const STROKE = 1.75;
export const SIZE = 20;

/** A menu or list icon at the shared size and weight. */
export function icon(Glyph: LucideIcon) {
  function Sized() {
    return <Glyph size={SIZE} strokeWidth={STROKE} aria-hidden="true" />;
  }
  Sized.displayName = `Icon(${Glyph.displayName ?? "lucide"})`;
  return Sized;
}

/** Each resource and page with its own icon. */
export const ICONS = {
  work: ListTodo,
  traders: Contact,
  payouts: Wallet,
  cases: Gavel,
  onboardings: IdCard,
  progressions: BadgeCheck,
  apprenticeProposals: GraduationCap,
  notifications: Bell,
  members: Users,
  grants: KeyRound,
  cutoffs: UserX,
  proposals: FilePen,
  changes: History,
  news: CalendarClock,
  anomalies: Activity,
  audit: ShieldCheck,
  policies: ScrollText,
  site: Globe,
  methods: CreditCard,
  settings: SlidersHorizontal,
  training: BookOpen,
  duties: ClipboardList,
  staff: UserCog,
  next: ChevronRight,
  search: Search,
  // settings hub cards (rule AC05)
  general: Building2,
  brand: Palette,
  templates: MessageSquareText,
  withdrawals: Banknote,
  kyc: ScanFace,
  seo: FileSearch,
  languages: Languages,
  extensions: Puzzle,
  maintenance: Wrench,
  security: LockKeyhole,
} satisfies Record<string, LucideIcon>;
