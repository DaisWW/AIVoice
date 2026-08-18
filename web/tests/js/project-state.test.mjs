import assert from "node:assert/strict";
import test from "node:test";

import { JobController } from "../../static/js/jobs/job-controller.js";
import { ProjectController } from "../../static/js/projects/project-controller.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function element() {
  let markup = "";
  return {
    classList: { toggle() {} },
    dataset: {},
    disabled: false,
    hidden: false,
    textContent: "",
    value: "",
    addEventListener() {},
    querySelectorAll() { return []; },
    setAttribute() {},
    get innerHTML() { return markup; },
    set innerHTML(value) { markup = value; },
  };
}

function installProjectDom() {
  const selectors = [
    "#projectSelect", "#projectGrid", "#emptyProjectState", "#projectContent",
    "#openCreate", "#memberAddPanel", "#memberAddForm", "#leaveProject",
    "#currentProjectName", "#currentProjectDescription", "#projectMemberCount",
    "#projectVoiceCount", "#projectScriptCount", "#projectJobCount", "#memberList",
  ];
  const elements = new Map(selectors.map((selector) => [selector, element()]));
  globalThis.document = {
    hidden: false,
    querySelector: (selector) => elements.get(selector) || null,
  };
  return elements;
}

function project(id, name) {
  return {
    id, name, description: "", member_count: 1, voice_count: 0,
    script_count: 0, job_count: 0, member_role: "owner", can_manage: true,
  };
}

test("project member responses cannot overwrite a newer selection", async () => {
  const elements = installProjectDom();
  const alpha = project("alpha", "Alpha");
  const beta = project("beta", "Beta");
  const alphaMembers = deferred();
  const betaMembers = deferred();
  const state = {
    projects: [alpha, beta],
    projectId: null,
    get project() { return this.projects.find((item) => item.id === this.projectId); },
    selectProject(id) { this.projectId = id; },
  };
  const api = {
    get(path) {
      return path.includes("alpha") ? alphaMembers.promise : betaMembers.promise;
    },
  };
  const changes = [];
  const controller = new ProjectController({
    state,
    api,
    shell: {},
    onProjectChanged: (selected) => changes.push(selected.id),
  });

  const selectingAlpha = controller.select("alpha");
  const selectingBeta = controller.select("beta");
  betaMembers.resolve({
    members: [{ id: "b", username: "bob", display_name: "Bob", role: "owner" }],
  });
  await selectingBeta;
  alphaMembers.resolve({
    members: [{ id: "a", username: "alice", display_name: "Alice", role: "owner" }],
  });
  await selectingAlpha;

  assert.match(elements.get("#memberList").innerHTML, /Bob/);
  assert.doesNotMatch(elements.get("#memberList").innerHTML, /Alice/);
  assert.deepEqual(changes, ["beta"]);
});

test("job refresh ignores a response from the previous project", async () => {
  globalThis.document = { hidden: false };
  const response = deferred();
  const state = {
    projectId: "alpha",
    jobs: [{ id: "alpha-job" }],
    selectedJobId: null,
    isAdmin: false,
    get jobApiBase() { return "/api/jobs"; },
    selectJob(id) { this.selectedJobId = id; },
  };
  const controller = new JobController({
    state,
    api: { get: () => response.promise },
    shell: { renderOffline() {} },
    listView: { render() {} },
    detailView: {},
  });

  const refreshing = controller.refresh();
  state.projectId = "beta";
  state.jobs = [{ id: "beta-job" }];
  response.resolve({ jobs: [] });
  await refreshing;

  assert.deepEqual(state.jobs, [{ id: "beta-job" }]);
});

test("job refresh is idle when the user has no project", async () => {
  globalThis.document = { hidden: false };
  let requests = 0;
  const controller = new JobController({
    state: {
      projectId: null, jobs: [], selectedJobId: null, isAdmin: false,
      get jobApiBase() { return "/api/jobs"; },
      selectJob() {},
    },
    api: { get: async () => { requests += 1; return { jobs: [] }; } },
    shell: { renderOffline() {} },
    listView: { render() {} },
    detailView: {},
  });

  await controller.refresh();

  assert.equal(requests, 0);
});
