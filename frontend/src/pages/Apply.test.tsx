// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Apply } from "./Apply";
import { api, type Job } from "../lib/api";
vi.mock("../lib/api",async original=>{const actual=await original<typeof import('../lib/api')>();return {...actual,api:{...actual.api,jobs:vi.fn(),submitApplication:vi.fn()}};});
afterEach(cleanup);
it('formats PDF sections safely and shows submission success in a popup',async()=>{
 const description='FICTIONAL SAMPLE | For capstone testing only\n1\nROLE OVERVIEW\nBuild Python\nAPIs.\nREQUIRED SKILLS\nPython and SQL.';
 vi.mocked(api.jobs).mockResolvedValue([{job_id:1,title:'Python',description} as Job]);
 vi.mocked(api.submitApplication).mockResolvedValue({application_id:3,receipt:'Application #3 submitted'});
 render(<Apply />);
 await screen.findByRole('option',{name:/Python/});
 fireEvent.change(screen.getByLabelText('Select a job'),{target:{value:'1'}});
 expect(screen.getByRole('heading',{name:'Required Skills'})).toBeDefined();
 expect(screen.getByText('Build Python APIs.')).toBeDefined();
 fireEvent.change(screen.getByLabelText(/Upload your resume PDF/),{target:{files:[new File(['%PDF-test'],'resume.pdf',{type:'application/pdf'})]}});
 fireEvent.change(screen.getByLabelText('Full name'),{target:{value:'Test Candidate'}});
 fireEvent.change(screen.getByLabelText('Email address'),{target:{value:'test@example.com'}});
 fireEvent.click(screen.getByRole('button',{name:'Apply'}));
 await screen.findByRole('dialog');
 expect(screen.getByText('Your application has been submitted.')).toBeDefined();
 expect(screen.queryByText('Application #3 submitted')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'Done'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect((screen.getByLabelText('Full name') as HTMLInputElement).value).toBe('');
});
