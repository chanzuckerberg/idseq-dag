import os

from typing import Dict, Optional, Sequence, Set, Tuple

import idseq_dag.util.command as command
import idseq_dag.util.command_patterns as command_patterns

from idseq_dag.engine.pipeline_step import PipelineStep
from idseq_dag.util.cdhit_clusters import parse_clusters_file
from idseq_dag.util.count import READ_COUNTING_MODE, ReadCountingMode


class PipelineStepNonhostFastq(PipelineStep):
    # Either one or two input read files can be supplied.
    # Works for both FASTA and FASTQ, although non-host FASTQ is more useful.
    def run(self) -> None:
        self.run_with_tax_ids(None, None)
        if self.additional_attributes.get("use_taxon_whitelist"):
            betacoronaviruses = set([
                2697049,  # SARS-CoV2
                694002  # betacoronavirus genus
            ])
            self.run_with_tax_ids(betacoronaviruses, "betacoronavirus")

    def run_with_tax_ids(
        self,
        tax_ids: Optional[Set[int]],
        filename: Optional[str]
    ) -> None:
        assert (tax_ids and filename) or not (
            tax_ids or filename), 'Must be supplied with tax_ids and filename or neither'

        scratch_dir = os.path.join(self.output_dir_local, "scratch_nonhost_fastq")
        command.make_dirs(scratch_dir)
        self.nonhost_headers = [
            os.path.join(scratch_dir, "nonhost_headers_r1.txt"),
            os.path.join(scratch_dir, "nonhost_headers_r2.txt")
        ]

        # Assumed to be [R1.fastq, R2.fastq] if there are two read files.
        fastqs = self.input_files_local[0]

        nonhost_fasta = self.input_files_local[1][0]

        clusters_dict = None
        if READ_COUNTING_MODE == ReadCountingMode.COUNT_ALL:  # v4 and higher
            # NOTE: this will load the set of all original read headers, which
            # could be several GBs in the worst case.
            clusters_dict = parse_clusters_file(
                self.input_files_local[2][0],
                self.input_files_local[3][0]
            )

        if filename is None:
            output_fastqs = self.output_files_local()
        else:
            output_fastqs = [
                f"{os.path.dirname(fastq)}/{filename}__{os.path.basename(self.output_files_local()[i])}"
                for i, fastq in enumerate(fastqs)]
            self.additional_output_files_hidden.extend(output_fastqs)

        fastqs = self.unzip_files(fastqs)

        self.generate_nonhost_headers(nonhost_fasta, clusters_dict, tax_ids)

        for i in range(len(fastqs)):
            self.generate_nonhost_fastq(self.nonhost_headers[i], fastqs[i], output_fastqs[i])

        # Clean up scratch files.
        for nonhost_headers in self.nonhost_headers:
            os.remove(nonhost_headers)

    @staticmethod
    # Unzip files with gunzip if necessary.
    def unzip_files(fastqs: Sequence[str]) -> Sequence[str]:
        new_fastqs = []

        for fastq in fastqs:
            if fastq[-3:] == '.gz':
                command.execute(
                    command_patterns.SingleCommand(
                        cmd="gunzip",
                        args=[
                            "-f",
                            "-k",
                            fastq
                        ]
                    )
                )

                new_fastqs.append(fastq[:-3])
            else:
                new_fastqs.append(fastq)

        return new_fastqs

    @staticmethod
    # Extract the original FASTQ header from the fasta file.
    #
    # Example fasta line:
    # >family_nr:4070:family_nt:1903414:genus_nr:4107:genus_nt:586:species_nr:4081:species_nt:587:NR:ABI34274.1:NT:CP029736.1:A00111:123:HCMCTDMXX:1:1111:5575:4382/1
    #
    # We just want the part after NT:XX:
    # We also split based on /1 and /2.
    def extract_header_from_line(line: str) -> Tuple[int, str, Set[int]]:
        line = line.strip()
        if line[-2:] == "/1":
            read_index = 0
            line = line[:-2]
        elif line[-2:] == "/2":
            read_index = 1
            line = line[:-2]
        else:
            # If there is no suffix /1 or /2, then only one read file was supplied.
            read_index = 0

        fragments = line.split(":")
        nt_index = fragments.index("NT")
        header = ":".join(fragments[nt_index + 2:])

        annot_tax_ids = set(
            int(fragments[fragments.index(annot_type) + 1])
            for annot_type in [
                "species_nt",
                "species_nr",
                "genus_nt",
                "genus_nr",
            ]
        )

        return read_index, header, annot_tax_ids

    def generate_nonhost_headers(
        self,
        nonhost_fasta_file: str,
        clusters_dict: Dict[str, Tuple] = None,
        tax_ids: Set[int] = None
    ):
        with open(nonhost_fasta_file, "r") as input_file, \
                open(self.nonhost_headers[0], "w") as output_file_0, \
                open(self.nonhost_headers[1], "w") as output_file_1:
            for line in input_file:
                # Assumes that the header line in the nonhost_fasta starts with ">"
                if line[0] != ">":
                    continue
                read_index, header, annot_tax_ids = PipelineStepNonhostFastq.extract_header_from_line(line)
                if tax_ids and not tax_ids.intersection(annot_tax_ids):
                    continue
                output_file = output_file_0 if read_index == 0 else output_file_1
                output_file.write(header + "\n")
                if not clusters_dict:
                    continue
                other_headers = clusters_dict[header][1:]
                for header in other_headers:
                    output_file.write(header + "\n")

    @staticmethod
    # Use seqtk, which is orders of magnitude faster than Python for this particular step.
    def generate_nonhost_fastq(
        nonhost_headers: str,
        fastq: str,
        output_file: str
    ) -> None:
        command.execute(
            command_patterns.ShellScriptCommand(
                script=r'''seqtk subseq "$1" "$2" > "$3";''',
                args=[
                    fastq,
                    nonhost_headers,
                    output_file
                ]
            )
        )

    def count_reads(self):
        pass
