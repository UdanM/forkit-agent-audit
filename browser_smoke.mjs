const socket = new WebSocket(process.env.FORKIT_TEST_WS);
await new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = reject;
});

async function exercise() {
  const $ = selector => document.querySelector(selector);
  const all = selector => [...document.querySelectorAll(selector)];
  const button = (text, scope = "body") => all(scope + " button").find(el => el.textContent === text && el.offsetWidth);
  const check = (condition, message) => { if (!condition) throw Error(message); };
  const click = (label, scope) => { const el = button(label, scope); check(el, label); el.click(); };
  const wait = async (predicate, message) => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw Error(message);
  };
  const text = selector => $(selector).textContent;
  const has = (selector, phrase) => text(selector).includes(phrase);
  await wait(() => $("#mode")?.textContent, "Dashboard failed to initialize");
  if (has("#mode", "Device scan")) {
    const current = await (await fetch("/api/state")).json();
    click("Activity timeline");
    if (current.timeline_configured && current.archive) {
      click(current.timeline ? "Refresh activity timeline" : "Load activity timeline");
      await wait(() => has("#timeline-content", "A read-only run was linked to a file change"),
        "Live pilot review evidence was not loaded");
      $("#ask-question").value = "Which changes lack approval?";
      click("Ask", "#ask-form");
      await wait(() => has("#ask-answer", "Approval denied"), "Live audit approval was missing from Ask");
      click("Open audit record", "#ask-answer");
      check(!$("#archive-view").hidden && $(".audit-row details[open]"), "Live audit citation did not open");
      return "PASS: configured live pilot, audit approval evidence, and direct record navigation";
    }
    check(has("#timeline-content", "No live activity source connected"), "Timeline guidance missing");
    click("Event review");
    check(has("#events", "No live activity source connected"), "Review guidance missing");
    check($("#demo-link").getAttribute("href") === "/demo/", "Sample link missing");
    const demo = await (await fetch("/demo/api/state")).json();
    check(demo.demo && demo.timeline_configured, "Sample mode unavailable");
    return "PASS: live timeline and event review explain missing sources; separate sample mode is available";
  }
  click("Discover tools");
  await wait(() => all("#tools article").length === 3, "Expected three fixtures");
  for (const count of [1, 2]) {
    click("Register tool", "#tools");
    await wait(() => all("#tools code").length === count, "Tool did not register");
  }
  const ids = all("#tools code").map(el => el.textContent);
  check(new Set(ids).size === 2, "Two same-name tools merged");
  const exported = await (await fetch("/inventory.json")).text();
  check(ids.every(id => exported.includes(id)) && !exported.includes("fixture-secret"), "Unexpected inventory export");
  click("Scan changed fixture");
  await wait(() => has("#tools", "Enabled") && has("#tools", "Disabled"), "Metadata change missing");
  check(ids.every(id => text("#tools").includes(id)), "Changed tool lost its ID");
  click("Event review");
  check(button("Link event").disabled, "Link allowed without consent");
  $("#events input").click();
  click("Link event");
  await wait(() => has("#events", "Linked"), "Event did not link");
  check(has("#events", "Approval missing"), "Missing approval was hidden");
  click("Record incident");
  await wait(() => has("#events", "Incident recorded"), "Incident did not save");
  click("Activity timeline");
  click("Load activity timeline");
  await wait(() => all("#timeline-content select").length >= 3, "Timeline did not load");
  click("Unknown actor2", ".activity-chart");
  check(all(".activity-line").length === 2 && $(".chart-bar.unknown").getAttribute("aria-pressed") === "true",
    "Chart selection did not filter to the two unknown changes");
  click("Unknown actor2", ".activity-chart");
  check(all(".activity-line").length === 12, "Chart selection did not reset");
  const [left, right] = $(".comparison-result").parentElement.querySelectorAll("select");
  const choose = (el, name) => {
    el.value = [...el.options].find(option => option.textContent.startsWith(name)).value;
    el.dispatchEvent(new Event("change"));
  };
  choose(left, "Editor run");
  choose(right, "Editor helper run");
  check(has(".comparison-result", "same tool"), "Helper relationship missing");
  choose(left, "Desktop run");
  check(has(".comparison-files", "reports/shared/summary.md"), "Shared path missing");
  choose(left, "Helper as reported");
  check(has(".comparison-result", "Cannot tell"), "Cross-source run was assumed");
  check(has("#timeline-content", "A log changed after it was saved"), "Edited log finding missing");
  const search = $("#timeline-content input[type=search]");
  search.value = "draft.md";
  search.dispatchEvent(new Event("input"));
  check(all(".activity-line").length === 1 && has(".activity-list", "Who acted is unknown"), "Unknown-actor file filter");
  $("#ask-question").value = "Who edited the status file?";
  click("Ask", "#ask-form");
  await wait(() => has("#ask-answer", "demo-helper"), "Assistant did not cite the recorded run");
  check(has("#ask-answer", "+ready"), "Assistant did not cite the saved change");
  click("Open audit record", "#ask-answer");
  check(!$("#archive-view").hidden && $("#audit-record-0 details").open, "Assistant did not open audit evidence");
  $("#ask-question").value = "Who changed reports/editor/draft.md?";
  click("Ask", "#ask-form");
  await wait(() => has("#ask-answer", "Actor unknown"), "Assistant attributed an unknown action");
  click("Show timeline records", "#ask-answer");
  check(!$("#timeline-view").hidden && all(".activity-line").length === 1, "Assistant did not focus the timeline");
  $("#ask-question").value = "What needs review?";
  click("Ask", "#ask-form");
  await wait(() => has("#ask-answer", "findings to review"), "Assistant did not answer a general review question");
  click("Open finding", "#ask-answer");
  check($("#timeline-content details[open] article[id^=finding-]"), "Assistant did not open review evidence");
  $("#ask-question").value = "Which changes lack approval?";
  click("Ask", "#ask-form");
  await wait(() => has("#ask-answer", "Approval denied"), "Timeline action approval was missing from Ask");
  return "PASS: discovery, registration, export, event review, timeline, chart, and record-grounded answers with navigation";
}

try {
  for (let id = 1; ; id++) {
    try {
      const result = await new Promise((resolve, reject) => {
        socket.onmessage = ({data}) => {
          const response = JSON.parse(data);
          if (response.id !== id) return;
          const details = response.result?.exceptionDetails;
          if (response.error || details) reject(Error(response.error?.message || details.exception?.description || details.text));
          else resolve(response.result.result.value);
        };
        socket.send(JSON.stringify({id, method: "Runtime.evaluate", params: {
          expression: "(" + exercise.toString() + ")()", awaitPromise: true, returnByValue: true
        }}));
      });
      console.log(result);
      break;
    } catch (error) {
      if (id >= 5 || !/context was destroyed|Cannot find default execution context/i.test(error.message)) throw error;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }
} finally {
  socket.close();
}
