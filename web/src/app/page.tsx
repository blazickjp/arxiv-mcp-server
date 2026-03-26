import Link from "next/link"
import {
  getKBStats,
  getPapers,
  getCollections,
  getSessions,
  getKGStats,
  type KBStats,
  type Paper,
  type Collection,
  type Session,
} from "@/lib/db"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

type DashboardData = {
  stats: KBStats | null
  papers: Paper[]
  collections: Collection[]
  sessions: Session[]
  kgStats: {
    nodes: Record<string, number>
    edges: Record<string, number>
  } | null
}

function loadDashboardData(): DashboardData {
  try {
    const stats = getKBStats()
    const papers = getPapers({ limit: 5 })
    const collections = getCollections()
    const sessions = getSessions()
    const kgStats = getKGStats()
    return { stats, papers, collections, sessions, kgStats }
  } catch {
    return {
      stats: null,
      papers: [],
      collections: [],
      sessions: [],
      kgStats: null,
    }
  }
}

function formatDate(dateStr: string): string {
  try {
    const date = new Date(dateStr)
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    })
  } catch {
    return dateStr
  }
}

function totalKGNodes(
  kgStats: DashboardData["kgStats"]
): number {
  if (!kgStats) return 0
  return Object.values(kgStats.nodes).reduce((sum, count) => sum + count, 0)
}

function statusColor(
  status: string
): "default" | "secondary" | "outline" {
  switch (status) {
    case "reading":
      return "default"
    case "completed":
      return "secondary"
    default:
      return "outline"
  }
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <h2 className="text-xl font-semibold mb-2">No papers yet</h2>
      <p className="text-muted-foreground max-w-md">
        Your knowledge base is empty. Add papers using the arxiv MCP server to
        see them here.
      </p>
    </div>
  )
}

function StatsRow({
  stats,
  collections,
  kgStats,
}: {
  stats: KBStats
  collections: Collection[]
  kgStats: DashboardData["kgStats"]
}) {
  const topStatus = Object.entries(stats.by_status).sort(
    (a, b) => b[1] - a[1]
  )[0]

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card size="sm">
        <CardHeader>
          <CardDescription>Total Papers</CardDescription>
          <CardTitle className="text-2xl">{stats.total_papers}</CardTitle>
        </CardHeader>
      </Card>

      <Card size="sm">
        <CardHeader>
          <CardDescription>Collections</CardDescription>
          <CardTitle className="text-2xl">{collections.length}</CardTitle>
        </CardHeader>
      </Card>

      <Card size="sm">
        <CardHeader>
          <CardDescription>Top Status</CardDescription>
          <CardTitle className="text-2xl capitalize">
            {topStatus ? `${topStatus[0]} (${topStatus[1]})` : "N/A"}
          </CardTitle>
        </CardHeader>
      </Card>

      <Card size="sm">
        <CardHeader>
          <CardDescription>KG Nodes</CardDescription>
          <CardTitle className="text-2xl">{totalKGNodes(kgStats)}</CardTitle>
        </CardHeader>
      </Card>
    </div>
  )
}

function RecentPapers({ papers }: { papers: Paper[] }) {
  if (papers.length === 0) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Papers</CardTitle>
        <CardDescription>
          Last 5 papers added to your knowledge base
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Tags</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Added</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {papers.map((paper) => (
              <TableRow key={paper.id}>
                <TableCell className="max-w-xs truncate font-medium">
                  <Link
                    href={`/papers/${paper.id}`}
                    className="hover:underline"
                  >
                    {paper.title}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{paper.source}</Badge>
                </TableCell>
                <TableCell>
                  <div className="flex gap-1 flex-wrap">
                    {paper.tags.slice(0, 3).map((tag) => (
                      <Badge key={tag} variant="secondary">
                        {tag}
                      </Badge>
                    ))}
                    {paper.tags.length > 3 && (
                      <Badge variant="secondary">
                        +{paper.tags.length - 3}
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={statusColor(paper.reading_status)}>
                    {paper.reading_status}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDate(paper.added_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function TagsCloud({ tags }: { tags: [string, number][] }) {
  if (tags.length === 0) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top Tags</CardTitle>
        <CardDescription>Most used tags across your papers</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {tags.map(([tag, count]) => (
            <Link key={tag} href={`/papers?tag=${encodeURIComponent(tag)}`}>
              <Badge variant="secondary" className="cursor-pointer">
                {tag}
                <span className="ml-1 text-muted-foreground">({count})</span>
              </Badge>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function ActiveSessions({ sessions }: { sessions: Session[] }) {
  const activeSessions = sessions.filter((s) => s.status === "active")

  if (activeSessions.length === 0) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Active Sessions</CardTitle>
        <CardDescription>Ongoing research sessions</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {activeSessions.map((session) => (
            <div
              key={session.id}
              className="flex items-center justify-between rounded-lg border p-3"
            >
              <div className="min-w-0 flex-1">
                <Link
                  href={`/sessions/${session.id}`}
                  className="font-medium hover:underline"
                >
                  {session.name}
                </Link>
                {session.goal && (
                  <p className="text-sm text-muted-foreground truncate">
                    {session.goal}
                  </p>
                )}
              </div>
              <div className="ml-4 flex gap-3 text-xs text-muted-foreground">
                {session.paper_count !== undefined && (
                  <span>{session.paper_count} papers</span>
                )}
                {session.finding_count !== undefined && (
                  <span>{session.finding_count} findings</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export default function DashboardPage() {
  const { stats, papers, collections, sessions, kgStats } =
    loadDashboardData()

  if (!stats) {
    return <EmptyState />
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground">
          Overview of your arXiv knowledge base
        </p>
      </div>

      <StatsRow stats={stats} collections={collections} kgStats={kgStats} />
      <RecentPapers papers={papers} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TagsCloud tags={stats.top_tags} />
        <ActiveSessions sessions={sessions} />
      </div>
    </div>
  )
}
