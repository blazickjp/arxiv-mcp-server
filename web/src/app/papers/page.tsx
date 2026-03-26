import Link from "next/link";
import { Suspense } from "react";
import { RiArticleLine, RiTimeLine } from "@remixicon/react";
import { getPapers } from "@/lib/db";
import { relativeTime, statusDotColor } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SearchInput } from "./search-input";
import { FilterPills } from "./filter-pills";

function truncateAuthors(authors: string[], max: number = 3): string {
  if (authors.length <= max) return authors.join(", ");
  return `${authors.slice(0, max).join(", ")} +${authors.length - max} more`;
}

function truncateAbstract(text: string | null, max: number = 150): string {
  if (!text) return "";
  if (text.length <= max) return text;
  return text.slice(0, max).trimEnd() + "...";
}

export default async function PapersPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const status = typeof params.status === "string" ? params.status : undefined;
  const source = typeof params.source === "string" ? params.source : undefined;
  const query = typeof params.q === "string" ? params.q : undefined;

  const papers = getPapers({
    reading_status: status,
    source,
    query,
  });

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8">
      <div className="mb-6 flex items-center gap-3">
        <RiArticleLine className="size-6 text-primary" />
        <h1 className="text-2xl font-semibold tracking-tight">Papers</h1>
        <span className="text-sm text-muted-foreground">
          {papers.length} paper{papers.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mb-4 space-y-3">
        <Suspense>
          <SearchInput />
        </Suspense>
        <Suspense>
          <FilterPills />
        </Suspense>
      </div>

      {papers.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <RiArticleLine className="mb-3 size-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">No papers found.</p>
          <p className="text-xs text-muted-foreground/70">
            Try adjusting your filters or search query.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {papers.map((paper) => (
            <Card key={paper.id} size="sm">
              <CardHeader>
                <CardTitle>
                  <Link
                    href={`/papers/${paper.id}`}
                    className="hover:text-primary transition-colors hover:underline underline-offset-2"
                  >
                    {paper.title}
                  </Link>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">
                    {truncateAuthors(paper.authors)}
                  </p>

                  {paper.abstract && (
                    <p className="text-xs text-muted-foreground/80 leading-relaxed">
                      {truncateAbstract(paper.abstract)}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="outline" className="gap-1">
                      <span
                        className={`inline-block size-1.5 rounded-full ${statusDotColor(paper.reading_status)}`}
                      />
                      {paper.reading_status}
                    </Badge>

                    <Badge variant="secondary">{paper.source}</Badge>

                    {paper.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-[10px]">
                        {tag}
                      </Badge>
                    ))}

                    <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                      <RiTimeLine className="size-3" />
                      {relativeTime(paper.added_at)}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
