import Link from "next/link";
import { connection } from "next/server";
import { getToolCalls, getHistoryStats } from "@/lib/db";
import { relativeTime } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const TOOL_CATEGORIES: Record<string, string> = {
  search_papers: "Search",
  arxiv_advanced_query: "Search",
  arxiv_semantic_search: "Search",
  download_paper: "Core",
  list_papers: "Core",
  read_paper: "Core",
  read_paper_chunks: "Core",
  arxiv_export: "Export",
  arxiv_compare_papers: "Analysis",
  arxiv_citation_graph: "Citations",
  arxiv_citation_context: "Citations",
  arxiv_research_lineage: "Citations",
  arxiv_trend_analysis: "Analysis",
  arxiv_research_digest: "Analysis",
  kb_save: "KB",
  kb_search: "KB",
  kb_list: "KB",
  kb_annotate: "KB",
  kb_remove: "KB",
  kg_query: "Graph",
  research_context: "Sessions",
};

function toolCategory(name: string): string {
  return TOOL_CATEGORIES[name] ?? "Other";
}

function categoryColor(cat: string): string {
  const colors: Record<string, string> = {
    Search: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    Core: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200",
    Export: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
    Analysis: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
    Citations: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
    KB: "bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200",
    Graph: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-200",
    Sessions: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200",
  };
  return colors[cat] ?? "bg-gray-100 text-gray-800";
}

function summarizeArgs(args: string): string {
  try {
    const parsed = JSON.parse(args);
    const key =
      parsed.query ?? parsed.topic ?? parsed.paper_id ?? parsed.paper_ids?.[0] ??
      parsed.source_id ?? parsed.action ?? parsed.title ?? "";
    return typeof key === "string" ? key.slice(0, 80) : JSON.stringify(key).slice(0, 80);
  } catch {
    return args.slice(0, 80);
  }
}

export default async function HistoryPage(props: {
  searchParams: Promise<{ tool?: string; q?: string }>;
}) {
  await connection();
  const searchParams = await props.searchParams;

  const stats = getHistoryStats();
  const calls = getToolCalls({
    tool_name: searchParams.tool,
    query: searchParams.q,
    limit: 100,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Research History</h1>
        <p className="text-sm text-muted-foreground">
          Every tool call auto-logged across Claude sessions
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total Calls
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.total_calls}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Last 24h
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.last_24h}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Last 7 Days
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.last_7d}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Errors
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600">{stats.total_errors}</div>
          </CardContent>
        </Card>
      </div>

      {/* Tool breakdown */}
      {Object.keys(stats.by_tool).length > 0 && (
        <div className="flex flex-wrap gap-2">
          <Link href="/history">
            <Badge variant={!searchParams.tool ? "default" : "outline"}>
              All ({stats.total_calls})
            </Badge>
          </Link>
          {Object.entries(stats.by_tool).map(([tool, count]) => (
            <Link key={tool} href={`/history?tool=${tool}`}>
              <Badge variant={searchParams.tool === tool ? "default" : "outline"}>
                {tool} ({count})
              </Badge>
            </Link>
          ))}
        </div>
      )}

      {/* Call list */}
      {calls.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            <p className="text-lg font-medium">No history yet</p>
            <p className="text-sm mt-1">
              Tool calls will appear here after you use Claude with the arXiv MCP server
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {calls.map((call) => {
            const cat = toolCategory(call.tool_name);
            return (
              <Link
                key={call.id}
                href={`/history/${call.id}`}
                className="block"
              >
                <Card className="hover:bg-muted/50 transition-colors">
                  <CardContent className="py-3 px-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3 min-w-0">
                        <Badge
                          variant="secondary"
                          className={`text-xs shrink-0 ${categoryColor(cat)}`}
                        >
                          {cat}
                        </Badge>
                        <span className="font-mono text-sm font-medium shrink-0">
                          {call.tool_name}
                        </span>
                        <span className="text-sm text-muted-foreground truncate">
                          {summarizeArgs(call.arguments)}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 shrink-0 text-xs text-muted-foreground">
                        {call.is_error ? (
                          <Badge variant="destructive" className="text-xs">
                            Error
                          </Badge>
                        ) : null}
                        {call.duration_ms != null && (
                          <span>{call.duration_ms}ms</span>
                        )}
                        <span className="text-xs">
                          {call.response_size > 1024
                            ? `${(call.response_size / 1024).toFixed(1)}KB`
                            : `${call.response_size}B`}
                        </span>
                        <span className="w-24 text-right">
                          {relativeTime(call.timestamp)}
                        </span>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
