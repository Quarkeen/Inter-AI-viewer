import { useEffect, useState } from "react";
import { ApiError, api, type Job } from "../lib/api";
import { Alert, Button, Card, Field, Input, PageTitle, Select, Spinner, Modal } from "../components/ui";

import { CheckCircle2 } from "lucide-react";
import { JobDescription } from "../components/JobDescription";

export function Apply() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [uploadKey, setUploadKey] = useState(0);
  const [resume, setResume] = useState<File | null>(null);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .jobs()
      .then(setJobs)
      .catch((err: unknown) =>
        setLoadError(err instanceof ApiError ? err.message : "Jobs could not be loaded. Please refresh and try again."),
      );
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setReceipt(null);
    setError(null);
    if (jobId == null) {
      setError("Select the job you want to apply for.");
      return;
    }
    if (!resume) {
      setError("Upload a readable resume PDF before applying.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await api.submitApplication(jobId, fullName, email, resume);
      setReceipt(result.receipt);
      setFullName("");
      setEmail("");
      setResume(null);
      setUploadKey(key => key + 1);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Your application could not be saved. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loadError) return <Alert tone="error">{loadError}</Alert>;
  if (!jobs) return <Spinner label="Loading jobs…" />;
  if (jobs.length === 0) return <Alert tone="info">There are no jobs available yet. Please check back later.</Alert>;

  const selected = jobs.find((job) => job.job_id === jobId);
  return (
    <div className="mx-auto max-w-2xl">
      <PageTitle title="Apply for a job" caption="Choose a role, add your details, and upload your resume." />
      {receipt && <Modal title="Application submitted" onClose={() => setReceipt(null)}>
        <div className="py-3 text-center"><CheckCircle2 className="mx-auto mb-4 h-12 w-12 text-emerald-600" aria-hidden /><p className="text-lg font-semibold">Your application has been submitted.</p><p className="mt-2 text-sm text-ink-500">The team will review your application and contact you about the next steps.</p><Button autoFocus className="mt-6" onClick={() => setReceipt(null)}>Done</Button></div>
      </Modal>}
      <Card>
        <form onSubmit={onSubmit} className="space-y-4">
          <Field label="Select a job">
            <Select value={jobId ?? ""} onChange={(event) => setJobId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">Choose the role you want to apply for</option>
              {jobs.map((job) => (
                <option key={job.job_id} value={job.job_id}>
                  {job.title} · Job #{job.job_id}
                </option>
              ))}
            </Select>
          </Field>
          {selected && <JobDescription text={selected.description} />}
          <Field label="Upload your resume PDF" hint="Up to 5 MB. Use a text-based PDF, not a scanned image.">
            <Input key={uploadKey} type="file" accept=".pdf" onChange={(event) => setResume(event.target.files?.[0] ?? null)} />
          </Field>
          <Field label="Full name">
            <Input value={fullName} maxLength={200} onChange={(event) => setFullName(event.target.value)} />
          </Field>
          <Field label="Email address">
            <Input
              value={email}
              maxLength={254}
              placeholder="name@example.com"
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          <Button type="submit" loading={submitting}>
            Apply
          </Button>
        </form>
        {error && (
          <div className="mt-4">
            <Alert tone="error">{error}</Alert>
          </div>
        )}
      </Card>
    </div>
  );
}
