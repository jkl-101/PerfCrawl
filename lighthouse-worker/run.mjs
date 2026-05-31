// PerfCrawl Lighthouse worker — D-02/D-04 boundary contract.
//
// One-shot Node subprocess invoked by the Python orchestrator. Stateless:
// argv -> lighthouse(url, flags, config) -> JSON-over-stdout -> exit 0/1.
//
// Source: synthesized from https://github.com/GoogleChrome/lighthouse/blob/main/docs/readme.md
// (programmatic-usage example) + Phase 2 RESEARCH § Pattern 2 + Pitfall 4
// (default config emulates mobile; desktop needs explicit screenEmulation +
// throttling override) + Pitfall 6 (result.report is a 2-array when flags.output
// is an array — destructure before serializing).
//
// Contract:
//   argv: --port=<n> --url=<u> --form-factor=<mobile|desktop>
//   stdout: JSON {lhr, reportJson, reportHtml}
//   stderr: error messages on failure
//   exit 0 success / exit 1 failure (Python collapses both retry+drop)
//
// Self-terminate after 55s (Assumption A5 defense-in-depth for D-14): the
// Python side will subprocess.run(timeout=60), but a 55s internal watchdog
// gives a cleaner error than a timeout-kill of an orphaned Chrome.

import lighthouse from "lighthouse";
import { parseArgs } from "node:util";

// --- A5: self-terminate watchdog (defense-in-depth for D-14) ----------------
const WATCHDOG_MS = 55_000;
const watchdog = setTimeout(() => {
  // IN-07: callback-form stderr write so the kernel finishes draining the
  // error line before the worker terminates. Mirrors the CR-01 drain-before-
  // exit pattern at the watchdog site for stderr consistency.
  process.stderr.write(
    `worker error: self-terminated after ${WATCHDOG_MS}ms watchdog\n`,
    () => process.exit(1),
  );
}, WATCHDOG_MS);
// Don't keep the event loop alive on the watchdog alone.
watchdog.unref?.();

const { values } = parseArgs({
  options: {
    port: { type: "string" },
    url: { type: "string" },
    "form-factor": { type: "string", default: "mobile" },
  },
});

if (!values.port || !values.url) {
  process.stderr.write("worker error: --port and --url are required\n");
  process.exit(1);
}

// IN-01: validate --form-factor against the known set BEFORE Lighthouse
// runs. The Python orchestrator already raises UserError on bad values, but
// the worker is a separate argv contract — a direct ``node run.mjs
// --form-factor=tablet ...`` invocation (Phase 3 scripts, debug sessions,
// the test suite) should not silently fall through to a confusing
// Lighthouse "Screen emulation does not match formFactor" deep-stack error.
// Mirrors the labeled-proxy defense-in-depth pattern: both layers enforce
// the contract independently.
const VALID_FORM_FACTORS = new Set(["mobile", "desktop"]);
if (!VALID_FORM_FACTORS.has(values["form-factor"])) {
  process.stderr.write(
    `worker error: --form-factor must be 'mobile' or 'desktop'; got ${JSON.stringify(values["form-factor"])}\n`,
  );
  process.exit(1);
}

const flags = {
  port: Number(values.port),
  output: ["json", "html"], // Pitfall 6: returns [jsonStr, htmlStr]
  logLevel: "error",         // keep stderr quiet; Python parses stdout
};

// Pitfall 4: LH's default config emulates mobile and `formFactor` alone is
// silently ignored for the screenEmulation/throttling shape. For desktop runs
// we must also override the screenEmulation + throttling presets to match the
// LH desktop preset (see LH 13 docs/emulation.md).
const desktopOverrides =
  values["form-factor"] === "desktop"
    ? {
        screenEmulation: {
          mobile: false,
          width: 1350,
          height: 940,
          deviceScaleFactor: 1,
          disabled: false,
        },
        throttling: {
          rttMs: 40,
          throughputKbps: 10240,
          cpuSlowdownMultiplier: 1,
          requestLatencyMs: 0,
          downloadThroughputKbps: 0,
          uploadThroughputKbps: 0,
        },
      }
    : {};

const config = {
  extends: "lighthouse:default",
  settings: {
    formFactor: values["form-factor"], // 'mobile' | 'desktop' (RUN-01)
    ...desktopOverrides,
    // throttlingMethod: 'simulate' is the LH default and satisfies RUN-02.
  },
};

try {
  const result = await lighthouse(values.url, flags, config);
  // Pitfall 6: result.report is a 2-element array because flags.output is
  // ['json', 'html']. Destructure before serializing.
  const [reportJson, reportHtml] = result.report;
  const payload = JSON.stringify({ lhr: result.lhr, reportJson, reportHtml });
  // CR-01: drain stdout before exiting. Real LH payloads are 200KB-2MB; the
  // kernel pipe buffer is ~64KB on Linux, so a synchronous process.exit()
  // after stdout.write() truncates the JSON. Callback form guarantees the
  // kernel finishes the drain before the worker process terminates.
  // IN-08: clearTimeout(watchdog) used to run BEFORE process.stdout.write,
  // which defeated the A5 defense-in-depth — the watchdog was specifically
  // there to fire if the longest-running operation (the payload write) hung
  // past the budget. Move clearTimeout INSIDE the callback so the watchdog's
  // lease covers the write itself; if the consumer dies mid-write and the
  // callback never fires, the timer still fires.
  process.stdout.write(payload, (err) => {
    clearTimeout(watchdog);
    if (err) {
      // IN-07: callback-form stderr drain on the error branch too.
      process.stderr.write(
        `worker error: stdout write failed: ${err.message}\n`,
        () => process.exit(1),
      );
      return;
    }
    process.exit(0);
  });
  // Do NOT call process.exit synchronously after this point.
} catch (err) {
  clearTimeout(watchdog);
  // IN-07: callback-form stderr drain on the top-level error branch too.
  process.stderr.write(
    `worker error: ${err.message}\n`,
    () => process.exit(1),
  );
}
