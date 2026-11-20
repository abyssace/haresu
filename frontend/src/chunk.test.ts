import { expect, test } from "vitest";
import { chunk } from "./components/Inspector";

test("a short file is a single document named after the file", () => {
  const docs = chunk("prices.md", "Each box costs 12 dollars.");
  expect(docs).toHaveLength(1);
  expect(docs[0].title).toBe("prices.md");
});

test("a long file is split by paragraph without losing text", () => {
  const paras = Array.from({ length: 30 }, (_, i) => `Paragraph ${i} ` + "x".repeat(200));
  const docs = chunk("long.txt", paras.join("\n\n"), 1200);
  expect(docs.length).toBeGreaterThan(4);
  expect(docs.every((d) => d.text.length <= 1200)).toBe(true);
  expect(docs[0].title).toBe(`long.txt · part 1 of ${docs.length}`);
  expect(docs.map((d) => d.text).join("\n\n")).toBe(paras.join("\n\n"));
  expect(new Set(docs.map((d) => d.id)).size).toBe(docs.length);
});

test("a huge paragraph is also split", () => {
  const docs = chunk("block.txt", "y".repeat(3000), 1200);
  expect(docs.map((d) => d.text.length)).toEqual([1200, 1200, 600]);
});
