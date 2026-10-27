// Playwright: the one place this project renders the page. Everything here is
// development-only; the page itself ships no JavaScript.
//
// The page is opened over file:// so the browser checks make no network call
// and need no server: index.html and tokens.css are the whole site.
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/browser",
  reporter: [["list"]],
  forbidOnly: !!process.env.CI,
  use: {
    browserName: "chromium",
  },
});
