import { describe, expect, it } from "vitest";
import { age, areaOf, byPriority, monthGrid, plainBody, plainError, urgency } from "./work";

const NOW = 1_000_000_000_000;
const H = 3_600_000;

describe("the work queue", () => {
  it("puts overdue first, then soonest due, then longest waiting", () => {
    const items = [
      { id: "old", kind: "case", since: NOW - 50 * H, due_at: null },
      { id: "late", kind: "payout_authorise", since: NOW - H, due_at: NOW - 2 * H },
      { id: "soon", kind: "grant", since: NOW - H, due_at: NOW + 3 * H },
      { id: "later", kind: "grant", since: NOW - H, due_at: NOW + 72 * H },
      { id: "new", kind: "case", since: NOW - H, due_at: null },
    ];
    expect(byPriority(items, NOW).map((i) => i.id)).toEqual(["late", "soon", "later", "old", "new"]);
    expect(urgency({ kind: "case", since: NOW, due_at: NOW - H }, NOW)).toBe("overdue");
    expect(urgency({ kind: "case", since: NOW, due_at: NOW + 3 * H }, NOW)).toBe("due_soon");
    expect(urgency({ kind: "case", since: NOW, due_at: null }, NOW)).toBe("waiting");
  });

  it("names areas and ages", () => {
    expect(areaOf("payout_pass")).toBe("Payouts");
    expect(areaOf("screening_check")).toBe("Staff");
    expect(areaOf("news_gap")).toBe("News");
    expect(age(30_000)).toBe("now");
    expect(age(12 * 60_000)).toBe("12 m");
    expect(age(5 * H + 10 * 60_000)).toBe("5 h 10 m");
    expect(age(76 * H)).toBe("3 d 4 h");
  });
});

describe("the news desk", () => {
  it("says what went wrong in plain words", () => {
    expect(plainError("HTTPStatusError: Client error '403 Forbidden' for url 'https://x'\nFor more information")).toContain("refused the collector (403 Forbidden)");
    expect(plainError("ReadTimeout: ")).toBe("The publisher's site did not answer in time.");
    expect(plainError("HTTPStatusError: Server error '503 Service Unavailable' for url 'https://x'")).toContain("503 Service Unavailable");
    expect(plainError("something odd")).toBe("something odd");
  });

  it("rewrites the error inside a notification", () => {
    const body =
      "The news collector cannot read bls (since 2026-11-06 13:10 UTC). Error: HTTPStatusError: Client error '403 Forbidden' for url 'https://www.bls.gov/x' For more information check: https://developer.mozilla.org/x These restricted events are not being collected.";
    expect(plainBody(body)).toBe(
      "The news collector cannot read bls (since 2026-11-06 13:10 UTC). The publisher's site refused the collector (403 Forbidden): it blocks automated readers. These restricted events are not being collected.",
    );
    expect(plainBody("No error here.")).toBe("No error here.");
  });

  it("lays a month out Monday first", () => {
    const nov = monthGrid("2026-11"); // 1 November 2026 is a Sunday
    expect(nov[0]).toEqual([null, null, null, null, null, null, 1]);
    expect(nov.at(-1)).toEqual([30, null, null, null, null, null, null]);
    expect(nov.flat().filter((d) => d !== null)).toHaveLength(30);
  });
});
