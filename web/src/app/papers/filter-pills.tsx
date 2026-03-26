"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";
import { Button } from "@/components/ui/button";

const STATUS_OPTIONS = [
  { label: "All", value: "" },
  { label: "Unread", value: "unread" },
  { label: "Reading", value: "reading" },
  { label: "Completed", value: "completed" },
  { label: "Archived", value: "archived" },
] as const;

const SOURCE_OPTIONS = [
  { label: "All Sources", value: "" },
  { label: "arXiv", value: "arxiv" },
  { label: "Manual", value: "manual" },
] as const;

function FilterPills(): React.ReactElement {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [, startTransition] = useTransition();

  const currentStatus = searchParams.get("status") ?? "";
  const currentSource = searchParams.get("source") ?? "";

  const setFilter = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) {
        params.set(key, value);
      } else {
        params.delete(key);
      }
      startTransition(() => {
        router.push(`/papers?${params.toString()}`);
      });
    },
    [router, searchParams]
  );

  return (
    <div className="flex flex-wrap gap-4">
      <div className="flex flex-wrap gap-1">
        {STATUS_OPTIONS.map((opt) => (
          <Button
            key={opt.value}
            variant={currentStatus === opt.value ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("status", opt.value)}
          >
            {opt.label}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {SOURCE_OPTIONS.map((opt) => (
          <Button
            key={opt.value}
            variant={currentSource === opt.value ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("source", opt.value)}
          >
            {opt.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

export { FilterPills };
