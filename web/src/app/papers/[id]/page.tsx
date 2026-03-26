import Link from "next/link";
import {
  RiArrowLeftLine,
  RiExternalLinkLine,
  RiCalendarLine,
  RiUserLine,
  RiFolder2Line,
} from "@remixicon/react";
import { getPaper } from "@/lib/db";
import { relativeTime, statusDotColor } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  ReadingStatusSelector,
  TagsEditor,
  NotesEditor,
  DeletePaperButton,
} from "./paper-actions";

export default async function PaperDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const paper = getPaper(id);

  if (!paper) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-8">
        <Link href="/papers">
          <Button variant="ghost" size="sm" className="mb-4">
            <RiArrowLeftLine className="size-4" />
            Back to Papers
          </Button>
        </Link>
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-lg font-medium">Paper not found</p>
          <p className="text-sm text-muted-foreground">
            The paper you are looking for does not exist or has been removed.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[680px] px-4 py-10">
      <div className="mb-8 flex items-center justify-between">
        <Link href="/papers">
          <Button variant="ghost" size="sm">
            <RiArrowLeftLine className="size-4" />
            Back to Papers
          </Button>
        </Link>
        <DeletePaperButton paperId={paper.id} />
      </div>

      <h1 className="mb-5 text-3xl font-semibold leading-tight tracking-tight">
        {paper.title}
      </h1>

      {/* Metadata row */}
      <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
        {paper.authors.length > 0 && (
          <span className="flex items-center gap-1.5">
            <RiUserLine className="size-3.5" />
            {paper.authors.join(", ")}
          </span>
        )}

        {paper.published_date && (
          <span className="flex items-center gap-1.5">
            <RiCalendarLine className="size-3.5" />
            {paper.published_date}
          </span>
        )}

        <Badge variant="secondary">{paper.source}</Badge>

        {paper.categories.map((cat) => (
          <Badge key={cat} variant="secondary" className="text-[10px]">
            {cat}
          </Badge>
        ))}
      </div>

      {/* Status + tags row */}
      <div className="mb-8 space-y-3">
        <div className="flex flex-wrap items-center gap-4">
          <ReadingStatusSelector
            paperId={paper.id}
            currentStatus={paper.reading_status}
          />
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className={`inline-block size-2 rounded-full ${statusDotColor(paper.reading_status)}`}
            />
            Added {relativeTime(paper.added_at)}
          </span>
        </div>
        <TagsEditor paperId={paper.id} currentTags={paper.tags} />
      </div>

      <Separator className="mb-8" />

      {/* Abstract */}
      {paper.abstract && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Abstract</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-base leading-relaxed text-muted-foreground whitespace-pre-wrap">
              {paper.abstract}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Notes */}
      <Card className="mb-6 bg-muted/40">
        <CardContent className="pt-4">
          <NotesEditor paperId={paper.id} currentNotes={paper.notes} />
        </CardContent>
      </Card>

      {/* Collections */}
      {paper.collections && paper.collections.length > 0 && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <RiFolder2Line className="size-4" />
              Collections
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {paper.collections.map((col) => (
                <Badge key={col} variant="secondary">
                  {col}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* External link */}
      {paper.url && (
        <div className="mt-6">
          <a
            href={paper.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Button variant="outline" size="sm">
              <RiExternalLinkLine className="size-3.5" />
              View on arXiv
            </Button>
          </a>
        </div>
      )}
    </div>
  );
}
