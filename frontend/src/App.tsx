import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Apply } from "./pages/Apply";
import { Applications } from "./pages/Applications";
import { CreateJob } from "./pages/CreateJob";
import { InterviewTemplates } from "./pages/InterviewTemplates";
import { Interview } from "./pages/Interview";
import { Report } from "./pages/Report";
import { Schedule } from "./pages/Schedule";
import { Shortlist } from "./pages/Shortlist";
import { Shortlisted } from "./pages/Shortlisted";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<CreateJob />} />
          <Route path="apply" element={<Apply />} />
          <Route path="applications" element={<Applications />} />
          <Route path="shortlist" element={<Shortlist />} />
          <Route path="interview-templates" element={<InterviewTemplates />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="shortlisted" element={<Shortlisted />} />
          <Route path="candidate-report" element={<Report />} />
          <Route path="interview" element={<Interview />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
