import Link from "next/link";
import { Suspense } from "react";
import {
  RiSearchLine,
  RiExternalLinkLine,
  RiUserLine,
} from "@remixicon/react";
import { getPapers } from "@/lib/db";
import { Badge } from "@/components/ui/badge";
import { SearchForm } from "./search-form";

type SearchParams = Promise<{
  [key: string]: string | string[] | undefined;
}>;

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + "...";
}

async function SearchResults({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const q = typeof params.q === "string" ? params.q : undefined;
  const status =
    typeof params.status === "string" ? params.status : undefined;
  const source =
    typeof params.source === "string" ? params.source : undefined;

  if (!q) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <RiSearchLine className="mb-3 size-10 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">
          Search your knowledge base by title, abstract, or keywords.
        </p>
      </div>
    );
  }

  const papers = getPapers({
    query: q,
    reading_status: status,
    source,
    limit: 50,
  });

  if (papers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-sm text-muted-foreground">
          No results found for &ldquo;{q}&rdquo;. Try different keywords.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <p className="mb-4 text-xs text-muted-foreground">
        {papers.length} result{papers.length !== 1 ? "s" : ""} for &ldquo;{q}
        &rdquo;
      </p>
      {papers.map((paper) => (
        <Link
          key={paper.id}
          href={`/papers/${paper.id}`}
          className="group flex flex-col gap-1 rounded-lg px-3 py-3 transition-colors hover:bg-muted/40"
        >
          <div className="flex items-start justify-between gap-3">
            <h3 className="text-sm font-medium leading-snug group-hover:text-primary">
              {paper.title}
            </h3>
            {paper.url && (
              <RiExternalLinkLine className="mt-0.5 size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
            )}
          </div>
          {paper.authors.length > 0 && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <RiUserLine className="size-3" />
              {truncate(paper.authors.join(", "), 120)}
            </p>
          )}
          {paper.abstract && (
            <p className="text-xs leading-relaxed text-muted-foreground/80">
              {truncate(paper.abstract, 200)}
            </p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className="text-[10px]">
              {paper.reading_status}
            </Badge>
            <Badge variant="secondary" className="text-[10px]">
              {paper.source}
            </Badge>
            {paper.tags.slice(0, 3).map((tag) => (
              <Badge key={tag} variant="secondary" className="text-[10px]">
                {tag}
              </Badge>
            ))}
          </div>
        </Link>
      ))}
    </div>
  );
}

export default async function SearchPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Find papers across your entire knowledge base.
        </p>
      </div>

      <div className="mb-6">
        <Suspense>
          <SearchForm />
        </Suspense>
      </div>

      <SearchResults searchParams={searchParams} />
    </div>
  );
}
