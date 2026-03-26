"use client";

import { useState, useTransition } from "react";
import {
  RiDeleteBinLine,
  RiSaveLine,
  RiAddLine,
  RiCloseLine,
  RiAlertLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  updateNotes,
  updateReadingStatus,
  updateTags,
  deletePaper,
} from "@/actions/papers";

const READING_STATUSES = ["unread", "reading", "completed", "archived"] as const;

function ReadingStatusSelector({
  paperId,
  currentStatus,
}: {
  paperId: string;
  currentStatus: string;
}): React.ReactElement {
  const [isPending, startTransition] = useTransition();

  function handleChange(value: string | null): void {
    if (!value) return;
    startTransition(async () => {
      await updateReadingStatus(paperId, value);
    });
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-muted-foreground">Status:</span>
      <Select value={currentStatus} onValueChange={(v) => handleChange(v)} disabled={isPending}>
        <SelectTrigger size="sm" className="w-32">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {READING_STATUSES.map((status) => (
            <SelectItem key={status} value={status}>
              {status}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function TagsEditor({
  paperId,
  currentTags,
}: {
  paperId: string;
  currentTags: string[];
}): React.ReactElement {
  const [tags, setTags] = useState<string[]>(currentTags);
  const [newTag, setNewTag] = useState("");
  const [isPending, startTransition] = useTransition();

  function handleAddTag(): void {
    const trimmed = newTag.trim();
    if (!trimmed || tags.includes(trimmed)) return;
    const updated = [...tags, trimmed];
    setTags(updated);
    setNewTag("");
    startTransition(async () => {
      await updateTags(paperId, updated);
    });
  }

  function handleRemoveTag(tag: string): void {
    const updated = tags.filter((t) => t !== tag);
    setTags(updated);
    startTransition(async () => {
      await updateTags(paperId, updated);
    });
  }

  return (
    <div className="space-y-2">
      <span className="text-xs font-medium text-muted-foreground">Tags:</span>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Badge key={tag} variant="outline" className="gap-1">
            {tag}
            <button
              type="button"
              onClick={() => handleRemoveTag(tag)}
              disabled={isPending}
              className="ml-0.5 rounded-full hover:bg-muted-foreground/20 transition-colors"
              aria-label={`Remove tag ${tag}`}
            >
              <RiCloseLine className="size-3" />
            </button>
          </Badge>
        ))}
        <div className="flex gap-1">
          <Input
            type="text"
            placeholder="Add tag..."
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAddTag();
              }
            }}
            className="h-6 w-24 text-xs"
            disabled={isPending}
          />
          <Button
            variant="outline"
            size="xs"
            onClick={handleAddTag}
            disabled={isPending || !newTag.trim()}
          >
            <RiAddLine className="size-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function NotesEditor({
  paperId,
  currentNotes,
}: {
  paperId: string;
  currentNotes: string | null;
}): React.ReactElement {
  const [notes, setNotes] = useState(currentNotes ?? "");
  const [isPending, startTransition] = useTransition();
  const [saved, setSaved] = useState(false);

  function handleSave(): void {
    startTransition(async () => {
      await updateNotes(paperId, notes);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Notes</h2>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="text-xs text-green-600">Saved</span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={isPending}
          >
            <RiSaveLine className="size-3.5" />
            {isPending ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>
      <Textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Add notes about this paper..."
        className="min-h-32"
        disabled={isPending}
      />
    </div>
  );
}

function DeletePaperButton({
  paperId,
}: {
  paperId: string;
}): React.ReactElement {
  const [isPending, startTransition] = useTransition();

  function handleDelete(): void {
    startTransition(async () => {
      await deletePaper(paperId);
    });
  }

  return (
    <Dialog>
      <DialogTrigger
        render={
          <Button variant="destructive" size="sm" disabled={isPending} />
        }
      >
        <RiDeleteBinLine className="size-3.5" />
        Delete Paper
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RiAlertLine className="size-4 text-destructive" />
            Delete Paper
          </DialogTitle>
          <DialogDescription>
            Are you sure you want to delete this paper? This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={isPending}
          >
            {isPending ? "Deleting..." : "Yes, delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export { ReadingStatusSelector, TagsEditor, NotesEditor, DeletePaperButton };
