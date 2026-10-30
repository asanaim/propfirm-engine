import { parseLots, parsePrice, rejection } from "./price";

it("reads prices as integer points with the symbol's digits", () => {
  expect(parsePrice("1.10005", 5)).toBe(110_005);
  expect(parsePrice("1.1", 5)).toBe(110_000);
  expect(parsePrice(" 149.9 ", 3)).toBe(149_900);
  expect(parsePrice("2650", 2)).toBe(265_000);
  expect(parsePrice("80905.45", 2)).toBe(8_090_545);
  expect(parsePrice("42", 0)).toBe(42);
  for (const bad of ["", "1.100051", "-1.1", "1,1", "1e3", "abc", "0", "0.00000", "1.", ".5"]) {
    expect(parsePrice(bad, 5)).toBeNull();
  }
});

it("reads lots in hundredths", () => {
  expect(parseLots("0.01")).toBe(1);
  expect(parseLots("1")).toBe(100);
  expect(parseLots("2.5")).toBe(250);
  expect(parseLots("0.001")).toBeNull();
  expect(parseLots("0")).toBeNull();
});

it("explains an engine refusal", () => {
  expect(rejection("insufficient_margin")).toBe("Not enough free margin for this order.");
  expect(rejection("something_new")).toBe("Refused: something_new.");
  expect(rejection(undefined)).toBe("Refused.");
});
