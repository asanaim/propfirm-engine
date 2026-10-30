import { countries } from "./countries";

it("lists every officially assigned ISO 3166-1 code once, by name", () => {
  const list = countries();
  expect(list).toHaveLength(249);
  expect(new Set(list.map((c) => c.code)).size).toBe(249);
  expect(list.every((c) => /^[A-Z]{2}$/.test(c.code))).toBe(true);
  expect(list.find((c) => c.code === "DE")?.name).toBe("Germany");
  const names = list.map((c) => c.name);
  expect([...names].sort((a, b) => a.localeCompare(b, "en"))).toEqual(names);
});
