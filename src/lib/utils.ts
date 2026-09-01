import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const LEADING_ARTICLE = /^(the|a|an)\s+/i;

/**
 * Normalize a title for sorting by dropping a leading article ("the", "a",
 * "an") so e.g. "The Hobbit" sorts under "H".
 */
export function titleSortKey(title: string | null | undefined): string {
  return (title ?? "").trim().replace(LEADING_ARTICLE, "").toLowerCase();
}

/**
 * Format a rating to 2 decimal places
 */
export function formatRating(rating: number | null | undefined): string {
  if (rating == null || rating === 0) {
    return "0.00";
  }
  return rating.toFixed(2);
}
