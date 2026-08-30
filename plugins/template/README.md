# StoryCanvas plugin template

Copy this directory into a separate Python package, replace the manifest values,
and implement one bounded capability. The included example is a real
`prompt.compile.image` `PlanProcessor`, not a disconnected toy transform.
Plugins should depend on the public
`storycanvas_harness.sdk` and `storycanvas_harness.protocol` modules rather than
the execution engine or a concrete Host.

Every published plugin should include:

- a `storycanvas.plugin.toml` manifest;
- typed configuration and deterministic request identity;
- success, failure, resume, and conformance tests;
- explicit permissions and secret names;
- no secret values in Artifacts, Receipts, logs, or workflow JSON.

Expose a zero-argument factory through the standard Python entry-point group:

```toml
[project.entry-points."storycanvas.plugins"]
"community.example-plugin" = "plugin:create_plugin"
```

The entry-point name must equal `manifest.plugin_id`. StoryCanvas only imports
an installed entry point when an explicitly selected Profile names that Plugin.
