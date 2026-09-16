import { marked } from "marked";

export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false });
  return typeof html === "string" ? html : "";
}
