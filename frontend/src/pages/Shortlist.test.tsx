// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { api, type Application, type Job, type ShortlistItem, type ShortlistRun } from '../lib/api';
import { Shortlist } from './Shortlist';
vi.mock('../lib/api',async original=>{const actual=await original<typeof import('../lib/api')>();return {...actual,api:{...actual.api,jobs:vi.fn(),weights:vi.fn(),latestShortlist:vi.fn(),applications:vi.fn()}};});
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('shows new applicants before generation and refreshes arrivals without changing existing approvals',async()=>{
 vi.mocked(api.jobs).mockResolvedValue([{job_id:1,title:'Python'} as Job]);
 vi.mocked(api.weights).mockResolvedValue({weights:{required_skills:40}});
 vi.mocked(api.latestShortlist).mockResolvedValue({run:{run_id:'review',status:'completed'} as ShortlistRun,items:[{application_id:1,full_name:'Reviewed candidate',email:'one@example.com',score:80,approved:1,proposed:1} as ShortlistItem]});
 vi.mocked(api.applications).mockResolvedValue([{application_id:1},{application_id:2,full_name:'New candidate',email:'two@example.com'}] as Application[]);
 render(<MemoryRouter><Shortlist /></MemoryRouter>);
 await screen.findByText('New candidate');
 expect(screen.getByText('Not included in this review')).toBeDefined();
 const approved=screen.getByRole('checkbox',{name:'Approve for interview'}) as HTMLInputElement;
 expect(approved.checked && approved.disabled).toBe(true);
 vi.mocked(api.applications).mockResolvedValue([{application_id:1},{application_id:2,full_name:'New candidate',email:'two@example.com'},{application_id:3,full_name:'Third candidate',email:'three@example.com'}] as Application[]);
 fireEvent.focus(window);
 await screen.findByText('Third candidate');
 expect(approved.checked && approved.disabled).toBe(true);
});
