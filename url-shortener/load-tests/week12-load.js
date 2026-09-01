import http from "k6/http";
import { check } from "k6";

const RAW_BASE = __ENV.TARGET_BASE_URL;
const PROFILE = __ENV.PROFILE || "ramp";
const TEST_ITEM_ID = __ENV.TEST_ITEM_ID || "1";
const INGRESS_CONTROLLER = __ENV.INGRESS_CONTROLLER || "unknown";
const BYPASS_CACHE = (__ENV.BYPASS_CACHE || "false").toLowerCase() === "true";

if (!RAW_BASE) {
  throw new Error("TARGET_BASE_URL is required");
}

const BASE = RAW_BASE.replace(/\/+$/, "");

function numberEnv(name, fallback) {
  const value = Number(__ENV[name] || fallback);
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${name} must be a non-negative number`);
  }
  return value;
}

const preAllocatedVUs = numberEnv("PRE_ALLOCATED_VUS", 500);
const READ_DELAY_MS = numberEnv("READ_DELAY_MS", 0);

const rampScenario = {
  executor: "ramping-arrival-rate",
  startRate: numberEnv("RPS_LOW", 100),
  timeUnit: "1s",
  preAllocatedVUs,
  stages: [
    { target: numberEnv("RPS_LOW", 100), duration: __ENV.LOW_RAMP_DURATION || "3m" },
    { target: numberEnv("RPS_LOW", 100), duration: __ENV.LOW_HOLD_DURATION || "2m" },
    { target: numberEnv("RPS_MID", 1000), duration: __ENV.MID_RAMP_DURATION || "5m" },
    { target: numberEnv("RPS_MID", 1000), duration: __ENV.MID_HOLD_DURATION || "3m" },
    { target: numberEnv("RPS_HIGH", 5000), duration: __ENV.HIGH_RAMP_DURATION || "5m" },
    { target: numberEnv("RPS_HIGH", 5000), duration: __ENV.HIGH_HOLD_DURATION || "5m" },
    { target: 0, duration: __ENV.RAMP_DOWN_DURATION || "2m" },
  ],
  gracefulStop: "30s",
};

const steadyScenario = {
  executor: "constant-arrival-rate",
  rate: numberEnv("STEADY_RPS", 500),
  timeUnit: "1s",
  duration: __ENV.STEADY_DURATION || "12m",
  preAllocatedVUs,
  gracefulStop: "30s",
};

if (!["ramp", "steady"].includes(PROFILE)) {
  throw new Error("PROFILE must be ramp or steady");
}

export const options = {
  discardResponseBodies: true,
  tags: {
    ingress_controller: INGRESS_CONTROLLER,
    test_id: __ENV.TEST_ID || "week12-manual",
    profile: PROFILE,
  },
  scenarios: {
    requests: PROFILE === "ramp" ? rampScenario : steadyScenario,
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    checks: ["rate>0.95"],
    dropped_iterations: ["count==0"],
    "http_req_duration{endpoint:items_list}": ["p(95)<2000", "p(99)<5000"],
    "http_req_duration{endpoint:item_get}": ["p(95)<2000", "p(99)<5000"],
  },
};

function dataRequestParams(endpoint, name) {
  const headers = {};
  if (BYPASS_CACHE) {
    headers["X-Week12-Bypass-Cache"] = "true";
  }
  if (READ_DELAY_MS > 0) {
    headers["X-Week12-Read-Delay-Ms"] = String(READ_DELAY_MS);
  }

  return {
    headers,
    tags: { endpoint, name },
  };
}

export default function () {
  const choice = Math.random();
  let response;

  if (choice < 0.2) {
    response = http.get(`${BASE}/healthz`, {
      tags: { endpoint: "healthz", name: "GET /healthz" },
    });
  } else if (choice < 0.7) {
    response = http.get(
      `${BASE}/items`,
      dataRequestParams("items_list", "GET /items"),
    );
  } else {
    response = http.get(
      `${BASE}/items/${TEST_ITEM_ID}`,
      dataRequestParams("item_get", "GET /items/{item_id}"),
    );
  }

  check(response, {
    "status is 200": (result) => result.status === 200,
  });
}
