import { expect, test, beforeEach } from "vitest";
import {
  ALGORITHM_LAB_ROUTE_KEY,
  isMeaningfulAlgorithmLabSearch,
  readAlgorithmLabRoute,
  rememberAlgorithmLabRoute,
} from "./workspaceMemory";

beforeEach(() => {
  window.localStorage.clear();
});

test("only routes carrying a recording are meaningful", () => {
  expect(isMeaningfulAlgorithmLabSearch("?recording=rec_1&runA=a&runB=b")).toBe(true);
  expect(isMeaningfulAlgorithmLabSearch("?recording=rec_1")).toBe(true);
  expect(isMeaningfulAlgorithmLabSearch("")).toBe(false);
  expect(isMeaningfulAlgorithmLabSearch("?tab=case")).toBe(false);
});

test("remember then read round-trips the route", () => {
  rememberAlgorithmLabRoute("?recording=rec_1&runA=a&runB=b");
  expect(readAlgorithmLabRoute()).toBe("/algorithm-lab?recording=rec_1&runA=a&runB=b");
});

test("reads null when nothing meaningful was stored", () => {
  expect(readAlgorithmLabRoute()).toBeNull();
  rememberAlgorithmLabRoute("?tab=case");
  expect(window.localStorage.getItem(ALGORITHM_LAB_ROUTE_KEY)).toBeNull();
});
