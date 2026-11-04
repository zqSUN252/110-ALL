import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: {
    baseURL: process.env.MCSTAR_TEST_URL || "http://127.0.0.1:5173",
    headless: true,
    trace: "off",
    screenshot: "off",
  },
  reporter: "list",
});
