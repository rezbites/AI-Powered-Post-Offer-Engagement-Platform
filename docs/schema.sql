--
-- PostgreSQL database dump
--

\restrict 6htr9PdN3VvEZpZcRvaI99pQNsG0NdU99XNZP1x2TyV8zlDXJTJ9l0LstCI4Loy

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ai_analyses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_analyses (
    id character varying(36) NOT NULL,
    candidate_id character varying(36) NOT NULL,
    input_hash character varying(64) NOT NULL,
    summary text NOT NULL,
    risk_level character varying(10) DEFAULT 'LOW'::character varying NOT NULL,
    risk_confidence double precision DEFAULT '0'::double precision NOT NULL,
    risk_rationale text DEFAULT ''::text NOT NULL,
    signals json NOT NULL,
    next_action character varying(40) DEFAULT 'NO_ACTION'::character varying NOT NULL,
    recommended_follow_up text DEFAULT ''::text NOT NULL,
    provider character varying(20) DEFAULT 'mock'::character varying NOT NULL,
    model character varying(80),
    prompt_version character varying(20) DEFAULT 'v1'::character varying NOT NULL,
    status character varying(20) DEFAULT 'valid'::character varying NOT NULL,
    latency_ms integer,
    tokens_in integer,
    tokens_out integer,
    raw_response text,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    model_confidence double precision,
    dropped_signals integer DEFAULT 0 NOT NULL,
    model_risk_level character varying(10)
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id character varying(36) NOT NULL,
    actor_id character varying(36),
    entity_type character varying(60) NOT NULL,
    entity_id character varying(36) NOT NULL,
    action character varying(40) NOT NULL,
    before json,
    after json,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: automation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.automation_runs (
    id character varying(36) NOT NULL,
    rule_key character varying(60) NOT NULL,
    trigger character varying(20) DEFAULT 'scheduled'::character varying NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    candidates_scanned integer DEFAULT 0 NOT NULL,
    actions_created integer DEFAULT 0 NOT NULL,
    actions_skipped integer DEFAULT 0 NOT NULL,
    status character varying(20) DEFAULT 'success'::character varying NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: candidate_stages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_stages (
    id character varying(36) NOT NULL,
    candidate_id character varying(36) NOT NULL,
    stage_id character varying(36) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    completed_at timestamp with time zone,
    completed_by character varying(36),
    due_date date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidates (
    id character varying(36) NOT NULL,
    name character varying(120) NOT NULL,
    email character varying(255) NOT NULL,
    phone character varying(32),
    role_title character varying(120) NOT NULL,
    location character varying(120) NOT NULL,
    offer_date date NOT NULL,
    joining_date date NOT NULL,
    recruiter_id character varying(36) NOT NULL,
    status character varying(30) DEFAULT 'offer_accepted'::character varying NOT NULL,
    journey_template_id character varying(36),
    last_interaction_at timestamp with time zone,
    risk_level character varying(10) DEFAULT 'LOW'::character varying NOT NULL,
    risk_confidence double precision DEFAULT '0'::double precision NOT NULL,
    risk_source character varying(10) DEFAULT 'rule'::character varying NOT NULL,
    risk_override_reason text,
    risk_overridden_by character varying(36),
    risk_overridden_at timestamp with time zone,
    last_analyzed_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: follow_up_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.follow_up_actions (
    id character varying(36) NOT NULL,
    candidate_id character varying(36) NOT NULL,
    rule_key character varying(60),
    dedupe_date date,
    title character varying(255) NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    recommended_action character varying(40) DEFAULT 'NO_ACTION'::character varying NOT NULL,
    due_date date,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    resolved_by character varying(36),
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: generated_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generated_messages (
    id character varying(36) NOT NULL,
    candidate_id character varying(36) NOT NULL,
    channel character varying(20) DEFAULT 'email'::character varying NOT NULL,
    subject character varying(255),
    body text NOT NULL,
    tone character varying(40) DEFAULT 'warm_professional'::character varying NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    approved_by character varying(36),
    approved_at timestamp with time zone,
    sent_at timestamp with time zone,
    provider character varying(20) DEFAULT 'mock'::character varying NOT NULL,
    model character varying(80),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: interactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.interactions (
    id character varying(36) NOT NULL,
    candidate_id character varying(36) NOT NULL,
    channel character varying(20) DEFAULT 'email'::character varying NOT NULL,
    direction character varying(10) DEFAULT 'outbound'::character varying NOT NULL,
    content text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    created_by character varying(36),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: journey_stages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.journey_stages (
    id character varying(36) NOT NULL,
    template_id character varying(36) NOT NULL,
    key character varying(60) NOT NULL,
    label character varying(120) NOT NULL,
    sequence integer NOT NULL,
    sla_days integer DEFAULT 7 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: journey_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.journey_templates (
    id character varying(36) NOT NULL,
    name character varying(120) NOT NULL,
    description text,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recruiters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recruiters (
    id character varying(36) NOT NULL,
    name character varying(120) NOT NULL,
    email character varying(255) NOT NULL,
    role character varying(20) DEFAULT 'recruiter'::character varying NOT NULL,
    password_hash character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_analyses ai_analyses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_analyses
    ADD CONSTRAINT ai_analyses_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: automation_runs automation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.automation_runs
    ADD CONSTRAINT automation_runs_pkey PRIMARY KEY (id);


--
-- Name: candidate_stages candidate_stages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_stages
    ADD CONSTRAINT candidate_stages_pkey PRIMARY KEY (id);


--
-- Name: candidates candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_pkey PRIMARY KEY (id);


--
-- Name: follow_up_actions follow_up_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follow_up_actions
    ADD CONSTRAINT follow_up_actions_pkey PRIMARY KEY (id);


--
-- Name: generated_messages generated_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_messages
    ADD CONSTRAINT generated_messages_pkey PRIMARY KEY (id);


--
-- Name: interactions interactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_pkey PRIMARY KEY (id);


--
-- Name: journey_stages journey_stages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_stages
    ADD CONSTRAINT journey_stages_pkey PRIMARY KEY (id);


--
-- Name: journey_templates journey_templates_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_templates
    ADD CONSTRAINT journey_templates_name_key UNIQUE (name);


--
-- Name: journey_templates journey_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_templates
    ADD CONSTRAINT journey_templates_pkey PRIMARY KEY (id);


--
-- Name: recruiters recruiters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recruiters
    ADD CONSTRAINT recruiters_pkey PRIMARY KEY (id);


--
-- Name: candidate_stages uq_candidate_stage; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_stages
    ADD CONSTRAINT uq_candidate_stage UNIQUE (candidate_id, stage_id);


--
-- Name: follow_up_actions uq_follow_up_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follow_up_actions
    ADD CONSTRAINT uq_follow_up_idempotency UNIQUE (candidate_id, rule_key, dedupe_date);


--
-- Name: journey_stages uq_journey_stage_template_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_stages
    ADD CONSTRAINT uq_journey_stage_template_key UNIQUE (template_id, key);


--
-- Name: journey_stages uq_journey_stage_template_sequence; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_stages
    ADD CONSTRAINT uq_journey_stage_template_sequence UNIQUE (template_id, sequence);


--
-- Name: ix_ai_analyses_candidate_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_analyses_candidate_hash ON public.ai_analyses USING btree (candidate_id, input_hash);


--
-- Name: ix_ai_analyses_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_analyses_candidate_id ON public.ai_analyses USING btree (candidate_id);


--
-- Name: ix_ai_analyses_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_analyses_created ON public.ai_analyses USING btree (created_at);


--
-- Name: ix_ai_analyses_input_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ai_analyses_input_hash ON public.ai_analyses USING btree (input_hash);


--
-- Name: ix_audit_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_entity ON public.audit_log USING btree (entity_type, entity_id, created_at);


--
-- Name: ix_audit_log_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_created_at ON public.audit_log USING btree (created_at);


--
-- Name: ix_automation_runs_rule_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_automation_runs_rule_key ON public.automation_runs USING btree (rule_key);


--
-- Name: ix_candidate_stages_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_stages_candidate_id ON public.candidate_stages USING btree (candidate_id);


--
-- Name: ix_candidate_stages_candidate_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_stages_candidate_status ON public.candidate_stages USING btree (candidate_id, status);


--
-- Name: ix_candidates_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_candidates_email ON public.candidates USING btree (email);


--
-- Name: ix_candidates_joining_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_joining_date ON public.candidates USING btree (joining_date);


--
-- Name: ix_candidates_last_interaction_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_last_interaction_at ON public.candidates USING btree (last_interaction_at);


--
-- Name: ix_candidates_location; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_location ON public.candidates USING btree (location);


--
-- Name: ix_candidates_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_name ON public.candidates USING btree (name);


--
-- Name: ix_candidates_recruiter_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_recruiter_id ON public.candidates USING btree (recruiter_id);


--
-- Name: ix_candidates_recruiter_joining; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_recruiter_joining ON public.candidates USING btree (recruiter_id, joining_date);


--
-- Name: ix_candidates_risk_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_risk_level ON public.candidates USING btree (risk_level);


--
-- Name: ix_candidates_role_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_role_title ON public.candidates USING btree (role_title);


--
-- Name: ix_candidates_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_status ON public.candidates USING btree (status);


--
-- Name: ix_candidates_status_risk; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidates_status_risk ON public.candidates USING btree (status, risk_level);


--
-- Name: ix_follow_up_actions_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_follow_up_actions_candidate_id ON public.follow_up_actions USING btree (candidate_id);


--
-- Name: ix_follow_ups_status_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_follow_ups_status_due ON public.follow_up_actions USING btree (status, due_date);


--
-- Name: ix_generated_messages_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_generated_messages_candidate_id ON public.generated_messages USING btree (candidate_id);


--
-- Name: ix_interactions_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interactions_candidate_id ON public.interactions USING btree (candidate_id);


--
-- Name: ix_interactions_candidate_occurred; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interactions_candidate_occurred ON public.interactions USING btree (candidate_id, occurred_at);


--
-- Name: ix_interactions_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_interactions_occurred_at ON public.interactions USING btree (occurred_at);


--
-- Name: ix_journey_stages_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_journey_stages_template_id ON public.journey_stages USING btree (template_id);


--
-- Name: ix_recruiters_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_recruiters_email ON public.recruiters USING btree (email);


--
-- Name: ai_analyses ai_analyses_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_analyses
    ADD CONSTRAINT ai_analyses_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id) ON DELETE CASCADE;


--
-- Name: audit_log audit_log_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: candidate_stages candidate_stages_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_stages
    ADD CONSTRAINT candidate_stages_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id) ON DELETE CASCADE;


--
-- Name: candidate_stages candidate_stages_completed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_stages
    ADD CONSTRAINT candidate_stages_completed_by_fkey FOREIGN KEY (completed_by) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: candidate_stages candidate_stages_stage_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_stages
    ADD CONSTRAINT candidate_stages_stage_id_fkey FOREIGN KEY (stage_id) REFERENCES public.journey_stages(id) ON DELETE CASCADE;


--
-- Name: candidates candidates_journey_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_journey_template_id_fkey FOREIGN KEY (journey_template_id) REFERENCES public.journey_templates(id) ON DELETE SET NULL;


--
-- Name: candidates candidates_recruiter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_recruiter_id_fkey FOREIGN KEY (recruiter_id) REFERENCES public.recruiters(id) ON DELETE RESTRICT;


--
-- Name: candidates candidates_risk_overridden_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidates
    ADD CONSTRAINT candidates_risk_overridden_by_fkey FOREIGN KEY (risk_overridden_by) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: follow_up_actions follow_up_actions_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follow_up_actions
    ADD CONSTRAINT follow_up_actions_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id) ON DELETE CASCADE;


--
-- Name: follow_up_actions follow_up_actions_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.follow_up_actions
    ADD CONSTRAINT follow_up_actions_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: generated_messages generated_messages_approved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_messages
    ADD CONSTRAINT generated_messages_approved_by_fkey FOREIGN KEY (approved_by) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: generated_messages generated_messages_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generated_messages
    ADD CONSTRAINT generated_messages_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id) ON DELETE CASCADE;


--
-- Name: interactions interactions_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.candidates(id) ON DELETE CASCADE;


--
-- Name: interactions interactions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.recruiters(id) ON DELETE SET NULL;


--
-- Name: journey_stages journey_stages_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.journey_stages
    ADD CONSTRAINT journey_stages_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.journey_templates(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 6htr9PdN3VvEZpZcRvaI99pQNsG0NdU99XNZP1x2TyV8zlDXJTJ9l0LstCI4Loy

