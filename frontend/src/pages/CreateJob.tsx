import { useState } from "react";
import { ApiError, api } from "../lib/api";
import { Alert, Button, Card, Field, Input, PageTitle, Textarea } from "../components/ui";

export function CreateJob() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);

  async function onFile(file: File | undefined) {
    setUploadError(null);
    if (!file) return;
    setUploading(true);
    try {
      const result = await api.extractDescription(file);
      setDescription(result.text);
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Upload a file smaller than 5 MB.");
    } finally {
      setUploading(false);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setNotice(null);
    setError(null);
    setSaving(true);
    try {
      const result = await api.createJob(title, description);
      setNotice(`Job #${result.job_id} saved: ${title.trim()}`);
      setTitle("");
      setDescription("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The job could not be saved. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageTitle title="HR Workspace" caption="Create a job" />
      <Card>
        <Field label="Upload a job description (optional)" hint="You can also type or paste below. Review uploaded text before saving.">
          <Input
            type="file"
            accept=".pdf,.txt"
            disabled={uploading}
            onChange={(event) => void onFile(event.target.files?.[0])}
          />
        </Field>
        {uploading && <p className="mt-2 text-sm text-ink-500">Reading the uploaded file…</p>}
        {uploadError && (
          <div className="mt-3">
            <Alert tone="error">{uploadError}</Alert>
          </div>
        )}
        <form onSubmit={onSubmit} className="mt-4 space-y-4">
          <Field label="Job title">
            <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Junior Python Backend Developer" />
          </Field>
          <Field label="Job description">
            <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={10} />
          </Field>
          <Button type="submit" loading={saving}>
            Save job
          </Button>
        </form>
        {notice && (
          <div className="mt-4">
            <Alert tone="success">{notice}</Alert>
          </div>
        )}
        {error && (
          <div className="mt-4">
            <Alert tone="error">{error}</Alert>
          </div>
        )}
      </Card>
    </div>
  );
}
