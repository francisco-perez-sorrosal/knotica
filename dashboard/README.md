# Knotica dashboard

The dashboard is a Preact MCP client, not a REST frontend. It reads only
`wiki_status` and `metrics_read` through the standalone server's streamable
HTTP endpoint.

```sh
cd dashboard
npm install
npm run build
```

The build emits one self-contained `dist/index.html`. Copy it into
`src/knotica/dashboard/app.html` before changing dashboard sources; CI verifies
that the checked-in wheel artifact is current. Run it with:

```sh
knotica mcp --http --port 8765
open 'http://127.0.0.1:8765/?topic=agentic-systems'
```

Use `?mcp=http://host:port/mcp` to target another standalone MCP server.
