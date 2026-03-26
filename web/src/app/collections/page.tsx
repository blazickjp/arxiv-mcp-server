import Link from "next/link";
import { connection } from "next/server";
import {
  RiFolder3Line,
  RiFileTextLine,
  RiCalendarLine,
  RiInboxLine,
} from "@remixicon/react";
import { getCollections } from "@/lib/db";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";

export default async function CollectionsPage() {
  await connection();
  const collections = getCollections();

  return (
    <div className="mx-auto w-full max-w-5xl px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Collections</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Browse papers organized into collections.
        </p>
      </div>

      {collections.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-20 text-center">
          <RiInboxLine className="mb-3 size-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            No collections yet. Save papers with collections via Claude.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {collections.map((collection) => (
            <Link
              key={collection.name}
              href={`/papers?collection=${encodeURIComponent(collection.name)}`}
            >
              <Card className="h-full transition-colors hover:bg-muted/30">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <RiFolder3Line className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{collection.name}</span>
                  </CardTitle>
                  {collection.description && (
                    <CardDescription className="line-clamp-2">
                      {collection.description}
                    </CardDescription>
                  )}
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <RiFileTextLine className="size-3.5" />
                      {collection.paper_count}{" "}
                      {collection.paper_count === 1 ? "paper" : "papers"}
                    </span>
                    <span className="flex items-center gap-1">
                      <RiCalendarLine className="size-3.5" />
                      {new Date(collection.created_at).toLocaleDateString()}
                    </span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
