"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { updatePaperAnnotation, removePaper } from "@/lib/db";

export async function updateNotes(paperId: string, notes: string): Promise<void> {
  updatePaperAnnotation(paperId, { notes });
  revalidatePath(`/papers/${paperId}`);
  revalidatePath("/papers");
}

export async function updateReadingStatus(paperId: string, status: string): Promise<void> {
  updatePaperAnnotation(paperId, { reading_status: status });
  revalidatePath(`/papers/${paperId}`);
  revalidatePath("/papers");
}

export async function updateTags(paperId: string, tags: string[]): Promise<void> {
  updatePaperAnnotation(paperId, { tags });
  revalidatePath(`/papers/${paperId}`);
  revalidatePath("/papers");
}

export async function deletePaper(paperId: string): Promise<void> {
  removePaper(paperId);
  revalidatePath("/papers");
  redirect("/papers");
}
