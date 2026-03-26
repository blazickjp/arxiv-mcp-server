import { connection } from "next/server";
import { getDataSources, getToolInfos, getStorageInfo, getKBStats, getHistoryStats } from "@/lib/db";
import { relativeTime } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Separator } from "@/components/ui/separator";

function statusBadge(status: string): React.ReactElement {
  const variants: Record<string, { color: string; label: string }> = {
    healthy: { color: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200", label: "Healthy" },
    degraded: { color: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200", label: "Degraded" },
    unavailable: { color: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200", label: "Unavailable" },
    unknown: { color: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200", label: "No Data" },
  };
  const v = variants[status] ?? variants.unknown;
  return <Badge variant="secondary" className={`text-xs ${v.color}`}>{v.label}</Badge>;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default async function SettingsPage() {
  await connection();

  const dataSources = getDataSources();
  const tools = getToolInfos();
  const storage = getStorageInfo();
  const kbStats = getKBStats();
  const historyStats = getHistoryStats();

  const totalDbSize = storage.reduce((sum, f) => sum + f.size_bytes, 0);

  // Group tools by category
  const toolsByCategory: Record<string, typeof tools> = {};
  for (const tool of tools) {
    if (!toolsByCategory[tool.category]) {
      toolsByCategory[tool.category] = [];
    }
    toolsByCategory[tool.category].push(tool);
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Data sources, tool usage, and storage health
        </p>
      </div>

      {/* Overview Stats */}
      <div className="grid grid-cols-5 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Data Sources
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-accent">{dataSources.length}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {dataSources.filter((s) => s.status === "healthy").length} healthy
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              MCP Tools
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-accent">{tools.length}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {tools.filter((t) => t.total_calls > 0).length} used
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Total Calls
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-accent">{historyStats.total_calls}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {historyStats.last_7d} last 7 days
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              KB Papers
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-accent">{kbStats.total_papers}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {kbStats.collections} collections
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Storage
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl font-bold text-accent">{formatBytes(totalDbSize)}</div>
            <p className="text-xs text-muted-foreground mt-1">
              {storage.filter((s) => s.exists).length} databases
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Data Sources */}
      <div>
        <h2 className="text-xl font-semibold mb-4">Data Sources</h2>
        <div className="grid grid-cols-1 gap-3">
          {dataSources.map((src) => (
            <Card key={src.slug}>
              <CardContent className="py-4 px-5">
                <div className="flex items-start justify-between">
                  <div className="space-y-1 min-w-0 flex-1">
                    <div className="flex items-center gap-3">
                      <h3 className="font-semibold text-base">{src.name}</h3>
                      {statusBadge(src.status)}
                      {!src.auth_configured && (
                        <Badge variant="outline" className="text-xs text-amber-600 border-amber-300">
                          Auth not configured
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">{src.description}</p>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground mt-2">
                      <span>Auth: {src.auth}</span>
                      <span className="font-mono text-[11px]">{src.base_url}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {src.tools.map((tool) => (
                        <Badge key={tool} variant="secondary" className="text-[11px] font-mono">
                          {tool}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-6 space-y-1">
                    <div className="text-lg font-semibold">{src.total_calls}</div>
                    <div className="text-xs text-muted-foreground">calls</div>
                    {src.error_calls > 0 && (
                      <div className="text-xs text-red-500">{src.error_calls} errors</div>
                    )}
                    {src.avg_duration_ms != null && (
                      <div className="text-xs text-muted-foreground">{src.avg_duration_ms}ms avg</div>
                    )}
                    {src.last_used && (
                      <div className="text-xs text-muted-foreground">
                        {relativeTime(src.last_used)}
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      <Separator />

      {/* Tools by Category */}
      <div>
        <h2 className="text-xl font-semibold mb-4">MCP Tools ({tools.length})</h2>
        {Object.entries(toolsByCategory).map(([category, categoryTools]) => (
          <div key={category} className="mb-6">
            <h3 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-2">
              {category} ({categoryTools.length})
            </h3>
            <div className="overflow-hidden rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[250px]">Tool</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Errors</TableHead>
                    <TableHead className="text-right">Avg Time</TableHead>
                    <TableHead className="text-right">Last Used</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {categoryTools.map((tool) => (
                    <TableRow key={tool.name}>
                      <TableCell className="font-mono text-sm">{tool.name}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{tool.source}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {tool.total_calls > 0 ? tool.total_calls : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {tool.error_calls > 0 ? (
                          <span className="text-red-500">{tool.error_calls} ({tool.error_rate}%)</span>
                        ) : tool.total_calls > 0 ? (
                          <span className="text-green-600">0</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {tool.avg_duration_ms != null ? (
                          <span>{tool.avg_duration_ms}ms</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        {tool.last_used ? relativeTime(tool.last_used) : (
                          <span className="text-muted-foreground">Never</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        ))}
      </div>

      <Separator />

      {/* Storage */}
      <div>
        <h2 className="text-xl font-semibold mb-4">Storage</h2>
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Database</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {storage.map((f) => (
                <TableRow key={f.file}>
                  <TableCell className="font-mono text-sm">{f.file}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {f.exists ? formatBytes(f.size_bytes) : "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    {f.exists ? (
                      <Badge variant="secondary" className="text-xs bg-green-100 text-green-800">Active</Badge>
                    ) : (
                      <Badge variant="secondary" className="text-xs">Not created</Badge>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell className="font-semibold">Total</TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {formatBytes(totalDbSize)}
                </TableCell>
                <TableCell />
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          All data stored locally at <code className="text-[11px]">~/.arxiv-mcp-server/papers/</code>
        </p>
      </div>
    </div>
  );
}
