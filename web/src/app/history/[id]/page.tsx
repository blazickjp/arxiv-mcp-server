import Link from "next/link";
import { connection } from "next/server";
import { getToolCall } from "@/lib/db";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RiArrowLeftLine } from "@remixicon/react";

export default async function HistoryDetailPage(props: {
  params: Promise<{ id: string }>;
}) {
  await connection();
  const params = await props.params;
  const callId = parseInt(params.id, 10);
  const call = getToolCall(callId);

  if (!call) {
    return (
      <div className="space-y-4">
        <Link href="/history">
          <Button variant="ghost" size="sm">
            <RiArrowLeftLine className="mr-1 h-4 w-4" /> Back to history
          </Button>
        </Link>
        <p className="text-muted-foreground">Tool call not found.</p>
      </div>
    );
  }

  let parsedArgs: Record<string, unknown> = {};
  try {
    parsedArgs = JSON.parse(call.arguments);
  } catch {
    /* ignore */
  }

  let parsedResponse: unknown = null;
  try {
    parsedResponse = JSON.parse(call.response_text ?? "");
  } catch {
    /* not JSON — show as text */
  }

  return (
    <div className="space-y-6">
      <Link href="/history">
        <Button variant="ghost" size="sm">
          <RiArrowLeftLine className="mr-1 h-4 w-4" /> Back to history
        </Button>
      </Link>

      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold font-mono">{call.tool_name}</h1>
        {call.is_error ? (
          <Badge variant="destructive">Error</Badge>
        ) : (
          <Badge variant="secondary">Success</Badge>
        )}
      </div>

      <div className="flex gap-4 text-sm text-muted-foreground">
        <span>{new Date(call.timestamp).toLocaleString()}</span>
        {call.duration_ms != null && <span>{call.duration_ms}ms</span>}
        <span>
          {call.response_size > 1024
            ? `${(call.response_size / 1024).toFixed(1)} KB`
            : `${call.response_size} B`}
        </span>
      </div>

      {/* Arguments */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Arguments</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="text-sm overflow-auto max-h-64 bg-muted p-3 rounded-md">
            {JSON.stringify(parsedArgs, null, 2)}
          </pre>
        </CardContent>
      </Card>

      {/* Response */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Response</CardTitle>
        </CardHeader>
        <CardContent>
          {parsedResponse ? (
            <pre className="text-sm overflow-auto max-h-[600px] bg-muted p-3 rounded-md">
              {JSON.stringify(parsedResponse, null, 2)}
            </pre>
          ) : (
            <pre className="text-sm overflow-auto max-h-[600px] bg-muted p-3 rounded-md whitespace-pre-wrap">
              {call.response_text}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
