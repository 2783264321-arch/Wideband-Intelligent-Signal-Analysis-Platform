import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { RecordingsPage } from "./RecordingsPage";

function setup() {
  const requests: { url: string; method: string; body: unknown; contentType: string | null }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    const method = options?.method ?? "GET";
    const body = options?.body as unknown;
    const contentType = (options?.headers as Record<string, string> | undefined)?.["Content-Type"] ?? null;
    requests.push({ url: String(url), method, body, contentType });
    if (url.includes("/api/recordings?limit=")) {
      return new Response(JSON.stringify({ items: [], total: 0 }));
    }
    if (url.endsWith("/api/datasets/spacenet/register") && method === "POST") {
      return new Response(JSON.stringify({ created: 2500, skipped: 0, invalid: 0, total: 2500 }), { status: 200 });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes><Route path="/" element={<RecordingsPage />} /></Routes>
    </MemoryRouter>,
  );
  return requests;
}

test("registers a server-local dataset path as JSON, not a multipart upload", async () => {
  const requests = setup();

  fireEvent.click(await screen.findByRole("button", { name: "Register SpaceNet Dataset" }));
  fireEvent.change(screen.getByLabelText("SpaceNet dataset path"), {
    target: { value: "D:\\LGFiles\\Wideband Signal Analysis Platform\\SpaceNet\\test" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Register" }));

  expect(await screen.findByText(/Created 2500 · Skipped 0 · Invalid 0/)).toBeInTheDocument();

  const registerCall = requests.find((item) => item.url.endsWith("/api/datasets/spacenet/register"));
  expect(registerCall).toBeTruthy();
  expect(registerCall!.method).toBe("POST");
  expect(registerCall!.contentType).toContain("application/json");
  expect(registerCall!.body).toEqual(JSON.stringify({
    dataset_path: "D:\\LGFiles\\Wideband Signal Analysis Platform\\SpaceNet\\test",
    split: "test",
  }));
});