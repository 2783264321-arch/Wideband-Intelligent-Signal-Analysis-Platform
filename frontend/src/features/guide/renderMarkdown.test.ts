import { expect, test } from "vitest";
import { renderMarkdown } from "./renderMarkdown";

test("renders a heading and list from markdown", () => {
  const html = renderMarkdown("# Title\n\n- one\n- two");
  expect(html).toContain("<h1");
  expect(html).toContain("<li");
});
