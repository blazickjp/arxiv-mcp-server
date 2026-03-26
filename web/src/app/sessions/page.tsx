import { connection } from "next/server";
import {
  RiFlaskLine,
  RiFileTextLine,
  RiChat3Line,
  RiLightbulbLine,
  RiTimeLine,
  RiInboxLine,
} from "@remixicon/react";
import { getSessions, type Session } from "@/lib/db";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";

const STATUS_ORDER: Record<string, number> = {
  active: 0,
  paused: 1,
  completed: 2,
};

const STATUS_STYLES: Record<
  string,
  { variant: "default" | "secondary" | "outline"; label: string }
> = {
  active: { variant: "default", label: "Active" },
  paused: { variant: "secondary", label: "Paused" },
  completed: { variant: "outline", label: "Completed" },
};

function groupByStatus(sessions: Session[]): Map<string, Session[]> {
  const sorted = [...sessions].sort(
    (a, b) =>
      (STATUS_ORDER[a.status] ?? 3) - (STATUS_ORDER[b.status] ?? 3)
  );

  const groups = new Map<string, Session[]>();
  for (const session of sorted) {
    const existing = groups.get(session.status) ?? [];
    existing.push(session);
    groups.set(session.status, existing);
  }
  return groups;
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
  return date.toLocaleDateString();
}

export default async function SessionsPage() {
  await connection();
  const sessions = getSessions();
  const grouped = groupByStatus(sessions);

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">
          Research Sessions
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Track multi-step research investigations and their findings.
        </p>
      </div>

      {sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
          <RiInboxLine className="mb-3 size-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            No research sessions yet. Start one via Claude with the
            research_context tool.
          </p>
        </div>
      ) : (
        <div className="space-y-8">
          {Array.from(grouped.entries()).map(([status, group]) => {
            const style = STATUS_STYLES[status] ?? {
              variant: "outline" as const,
              label: status,
            };
            return (
              <section key={status}>
                <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <span className="capitalize">{style.label}</span>
                  <span className="text-xs">({group.length})</span>
                </h2>
                <div className="grid gap-4 sm:grid-cols-2">
                  {group.map((session) => (
                    <SessionCard
                      key={session.id}
                      session={session}
                      statusStyle={style}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SessionCard({
  session,
  statusStyle,
}: {
  session: Session;
  statusStyle: { variant: "default" | "secondary" | "outline"; label: string };
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <RiFlaskLine className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{session.name}</span>
        </CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={statusStyle.variant} className="text-[10px]">
            {statusStyle.label}
          </Badge>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <RiTimeLine className="size-3" />
            {formatRelativeTime(session.updated_at)}
          </span>
        </div>
        {session.goal && (
          <CardDescription className="line-clamp-2">
            {session.goal}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <RiFileTextLine className="size-3.5" />
            {session.paper_count ?? 0} papers
          </span>
          <span className="flex items-center gap-1">
            <RiChat3Line className="size-3.5" />
            {session.thread_count ?? 0} threads
          </span>
          <span className="flex items-center gap-1">
            <RiLightbulbLine className="size-3.5" />
            {session.finding_count ?? 0} findings
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
