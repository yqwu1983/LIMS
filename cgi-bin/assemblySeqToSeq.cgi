#!/usr/bin/perl -w
use strict;
use CGI qw(:standard);
use CGI::Carp qw ( fatalsToBrowser ); 
use JSON; #JSON::XS is recommended to be installed for handling JSON string of big size 
use DBI;
use lib "lib/";
use lib "lib/pangu";
use pangu;
use user;
use userConfig;
use userCookie;

my $userCookie = new userCookie;
my $userId = (cookie('cid')) ? $userCookie->checkCookie(cookie('cid')) : 0;
exit if (!$userId);

my $user = new user;
my $userDetail = $user->getAllFieldsWithUserId($userId);
my $userName = $userDetail->{"userName"};

my $commoncfg = readConfig("main.conf");
my $userConfig = new userConfig;

my $alignEngineList;
$alignEngineList->{'blastn'} = "blast+/bin/blastn";
$alignEngineList->{'BLAT'} = "blat";
my $windowmasker = 'blast+/bin/windowmasker';
my $makeblastdb = 'blast+/bin/makeblastdb';

my $assemblyId = param ('assemblyId') || '';
my $identitySeqToSeq = param ('identitySeqToSeq') || $userConfig->getFieldValueWithUserIdAndFieldName($userId,"SEQTOSEQIDENTITY");
my $minOverlapSeqToSeq = param ('minOverlapSeqToSeq') || $userConfig->getFieldValueWithUserIdAndFieldName($userId,"SEQTOSEQMINOVERLAP");
my $redoAllSeqToSeq = param ('redoAllSeqToSeq') || '0';
my $speedyMode = param ('speedyMode') || '0';
my $checkGood = param ('checkGood') || '0';
my $task = param ('megablast') || 'blastn';

print header;

if($assemblyId)
{
	my $pid = fork();
	if ($pid) {
		print <<END;
<script>
	parent.closeDialog();
	parent.informationPop("It's running! This processing might take a while.");
</script>	
END
	}
	elsif($pid == 0){
		close (STDOUT);
		#connect to the mysql server
		my $dbh=DBI->connect("DBI:mysql:$commoncfg->{DATABASE}:$commoncfg->{DBHOST}",$commoncfg->{USERNAME},$commoncfg->{PASSWORD});
		my $assembly=$dbh->prepare("SELECT * FROM matrix WHERE id = ?");
		$assembly->execute($assemblyId);
		my @assembly = $assembly->fetchrow_array();
		my $target=$dbh->prepare("SELECT * FROM matrix WHERE id = ?");
		$target->execute($assembly[4]);
		my @target = $target->fetchrow_array();

		my $inAssemblySequenceId;
		my $assemblySeqs = $dbh->prepare("SELECT * FROM matrix WHERE container LIKE 'assemblySeq' AND o = ?");
		$assemblySeqs->execute($assemblyId);
		while(my @assemblySeqs = $assemblySeqs->fetchrow_array())
		{
			$inAssemblySequenceId->{$assemblySeqs[5]} = 1;
		}

		my $updateAssemblyToRunningSeqToSeq=$dbh->do("UPDATE matrix SET barcode = '-2' WHERE id = $assemblyId");

		my $assemblySequenceLength;
		open (SEQALL,">$commoncfg->{TMPDIR}/$assembly[4].$$.seq") or die "can't open file: $commoncfg->{TMPDIR}/$assembly[4].$$.seq";
		open (SEQNEW,">$commoncfg->{TMPDIR}/$assembly[4].$$.new.seq") or die "can't open file: $commoncfg->{TMPDIR}/$assembly[4].$$.new.seq";
		if($target[1] eq 'library')
		{
			my $getClones = $dbh->prepare("SELECT * FROM clones WHERE sequenced > 0 AND libraryId = ?");
			$getClones->execute($assembly[4]);
			while(my @getClones = $getClones->fetchrow_array())
			{
				my $getSequences = $dbh->prepare("SELECT * FROM matrix WHERE container LIKE 'sequence' AND o < 50 AND name LIKE ?");
				$getSequences->execute($getClones[1]);
				while(my @getSequences = $getSequences->fetchrow_array())
				{
					$assemblySequenceLength->{$getSequences[0]} = $getSequences[5];
					my $sequenceDetails = decode_json $getSequences[8];
					$sequenceDetails->{'id'} = '' unless (exists $sequenceDetails->{'id'});
					$sequenceDetails->{'description'} = '' unless (exists $sequenceDetails->{'description'});
					$sequenceDetails->{'sequence'} = '' unless (exists $sequenceDetails->{'sequence'});
					$sequenceDetails->{'sequence'} =~ tr/a-zA-Z/N/c; #replace nonword characters.;
					$sequenceDetails->{'gapList'} = '' unless (exists $sequenceDetails->{'gapList'});
					print SEQALL ">$getSequences[0]\n$sequenceDetails->{'sequence'}\n";
					print SEQNEW ">$getSequences[0]\n$sequenceDetails->{'sequence'}\n" if (!exists $inAssemblySequenceId->{$getSequences[0]});
				}
			}
		}
		if($target[1] eq 'genome')
		{
			my $getSequences = $dbh->prepare("SELECT * FROM matrix WHERE container LIKE 'sequence' AND o = 99 AND x = ?");
			$getSequences->execute($assembly[4]);
			while(my @getSequences = $getSequences->fetchrow_array())
			{
				$assemblySequenceLength->{$getSequences[0]} = $getSequences[5];
				my $sequenceDetails = decode_json $getSequences[8];
				$sequenceDetails->{'id'} = '' unless (exists $sequenceDetails->{'id'});
				$sequenceDetails->{'description'} = '' unless (exists $sequenceDetails->{'description'});
				$sequenceDetails->{'sequence'} = '' unless (exists $sequenceDetails->{'sequence'});
				$sequenceDetails->{'sequence'} =~ tr/a-zA-Z/N/c; #replace nonword characters.;
				$sequenceDetails->{'gapList'} = '' unless (exists $sequenceDetails->{'gapList'});
				print SEQALL ">$getSequences[0]\n$sequenceDetails->{'sequence'}\n";
				print SEQNEW ">$getSequences[0]\n$sequenceDetails->{'sequence'}\n" if (!exists $inAssemblySequenceId->{$getSequences[0]});
			}
		}
		close(SEQALL);
		close(SEQNEW);

		system( "$makeblastdb -in $commoncfg->{TMPDIR}/$assembly[4].$$.seq -dbtype nucl" );
		my $goodSequenceId;
		my $sequenceLength;
		if($redoAllSeqToSeq)
		{
			open (CMD,"$alignEngineList->{'blastn'} -query $commoncfg->{TMPDIR}/$assembly[4].$$.seq -task $task -db $commoncfg->{TMPDIR}/$assembly[4].$$.seq -dust no -evalue 1e-200 -perc_identity $identitySeqToSeq -max_target_seqs 10 -num_threads 8 -outfmt 6 |") or die "can't open CMD: $!";
		}
		else
		{
			open (CMD,"$alignEngineList->{'blastn'} -query $commoncfg->{TMPDIR}/$assembly[4].$$.new.seq -task $task -db $commoncfg->{TMPDIR}/$assembly[4].$$.seq -dust no -evalue 1e-200 -perc_identity $identitySeqToSeq -max_target_seqs 10 -num_threads 8 -outfmt 6 |") or die "can't open CMD: $!";
		}
		while(<CMD>)
		{
			/^#/ and next;
			my @hit = split("\t",$_);
			next if($hit[0] eq $hit[1]);
			next if($hit[3] < $minOverlapSeqToSeq);
			
			if($speedyMode)
			{
				my $deleteAlignmentFlag = 0;
				if($hit[0] < $hit[1])
				{
					unless(exists $goodSequenceId->{$hit[0]}->{$hit[1]})
					{
						$goodSequenceId->{$hit[0]}->{$hit[1]} = 1;
						$deleteAlignmentFlag = 1;
					}
				}
				else
				{
					unless(exists $goodSequenceId->{$hit[1]}->{$hit[0]})
					{
						$goodSequenceId->{$hit[1]}->{$hit[0]} = 1;
						$deleteAlignmentFlag = 1;
					}
				}
				if($deleteAlignmentFlag)
				{
					my $deleteAlignmentA = $dbh->do("DELETE FROM alignment WHERE query = $hit[0] AND subject = $hit[1]");
					my $deleteAlignmentB = $dbh->do("DELETE FROM alignment WHERE query = $hit[1] AND subject = $hit[0]");
				}
				my $insertAlignmentA=$dbh->prepare("INSERT INTO alignment VALUES ('', 'SEQtoSEQ\_1e-200\_$identitySeqToSeq\_$minOverlapSeqToSeq', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)");
				$insertAlignmentA->execute(@hit);

				#switch query and subject
				if($hit[8] < $hit[9])
				{
					my $exchange = $hit[8];
					$hit[8] = $hit[6];
					$hit[6] = $exchange;
					$exchange = $hit[9];
					$hit[9] = $hit[7];
					$hit[7] = $exchange;
					$exchange = $hit[1];
					$hit[1] = $hit[0];
					$hit[0] = $exchange;
				}
				else
				{
					my $exchange = $hit[8];
					$hit[8] = $hit[7];
					$hit[7] = $exchange;
					$exchange = $hit[9];
					$hit[9] = $hit[6];
					$hit[6] = $exchange;
					$exchange = $hit[1];
					$hit[1] = $hit[0];
					$hit[0] = $exchange;
				}

				my $insertAlignmentB=$dbh->prepare("INSERT INTO alignment VALUES ('', 'SEQtoSEQ\_1e-200\_$identitySeqToSeq\_$minOverlapSeqToSeq', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)");
				$insertAlignmentB->execute(@hit);
			}
			else
			{
				my $rerunBlastTwo = 0;
				if($hit[0] < $hit[1])
				{
					unless(exists $goodSequenceId->{$hit[0]}->{$hit[1]})
					{
						$goodSequenceId->{$hit[0]}->{$hit[1]} = 1;
						$rerunBlastTwo = 1;
					}
				}
				else
				{
					unless(exists $goodSequenceId->{$hit[1]}->{$hit[0]})
					{
						$goodSequenceId->{$hit[1]}->{$hit[0]} = 1;
						$rerunBlastTwo = 1;
					}
				}
				if($rerunBlastTwo)
				{
					my $deleteAlignmentA = $dbh->do("DELETE FROM alignment WHERE query = $hit[0] AND subject = $hit[1]");
					my $deleteAlignmentB = $dbh->do("DELETE FROM alignment WHERE query = $hit[1] AND subject = $hit[0]");

					unless(-e "$commoncfg->{TMPDIR}/$hit[0].$$.seq")
					{
						my $getSequenceA = $dbh->prepare("SELECT * FROM matrix WHERE id = ?");
						$getSequenceA->execute($hit[0]);
						my @getSequenceA =  $getSequenceA->fetchrow_array();
						open (SEQA,">$commoncfg->{TMPDIR}/$hit[0].$$.seq") or die "can't open file: $commoncfg->{TMPDIR}/$hit[0].$$.seq";
						my $sequenceDetailsA = decode_json $getSequenceA[8];
						$sequenceDetailsA->{'id'} = '' unless (exists $sequenceDetailsA->{'id'});
						$sequenceDetailsA->{'description'} = '' unless (exists $sequenceDetailsA->{'description'});
						$sequenceDetailsA->{'sequence'} = '' unless (exists $sequenceDetailsA->{'sequence'});
						$sequenceDetailsA->{'sequence'} =~ tr/a-zA-Z/N/c; #replace nonword characters.;
						$sequenceDetailsA->{'gapList'} = '' unless (exists $sequenceDetailsA->{'gapList'});
						print SEQA ">$getSequenceA[0]\n$sequenceDetailsA->{'sequence'}\n";
						close(SEQA);
					}
					unless(-e "$commoncfg->{TMPDIR}/$hit[1].$$.seq")
					{
						my $getSequenceB = $dbh->prepare("SELECT * FROM matrix WHERE id = ?");
						$getSequenceB->execute($hit[1]);
						my @getSequenceB =  $getSequenceB->fetchrow_array();
						open (SEQB,">$commoncfg->{TMPDIR}/$hit[1].$$.seq") or die "can't open file: $commoncfg->{TMPDIR}/$hit[1].$$.seq";
						my $sequenceDetailsB = decode_json $getSequenceB[8];
						$sequenceDetailsB->{'id'} = '' unless (exists $sequenceDetailsB->{'id'});
						$sequenceDetailsB->{'description'} = '' unless (exists $sequenceDetailsB->{'description'});
						$sequenceDetailsB->{'sequence'} = '' unless (exists $sequenceDetailsB->{'sequence'});
						$sequenceDetailsB->{'sequence'} =~ tr/a-zA-Z/N/c; #replace nonword characters.;
						$sequenceDetailsB->{'gapList'} = '' unless (exists $sequenceDetailsB->{'gapList'});
						print SEQB ">$getSequenceB[0]\n$sequenceDetailsB->{'sequence'}\n";
						close(SEQB);
					}
					my @alignments;
					my $goodOverlap = ($checkGood) ? 0 : 1;
					open (CMDA,"$alignEngineList->{'blastn'} -query $commoncfg->{TMPDIR}/$hit[0].$$.seq -subject $commoncfg->{TMPDIR}/$hit[1].$$.seq -dust no -evalue 1e-200 -perc_identity $identitySeqToSeq -outfmt 6 |") or die "can't open CMD: $!";
					while(<CMDA>)
					{
						/^#/ and next;
						my @detailedHit = split("\t",$_);
						if($detailedHit[3] >= $minOverlapSeqToSeq)
						{
							push @alignments, $_;
							if($detailedHit[6] == 1 || $detailedHit[7] == $assemblySequenceLength->{$detailedHit[0]})
							{
								$goodOverlap = 1;
							}
							#switch query and subject
							if($detailedHit[8] < $detailedHit[9])
							{
								my $exchange = $detailedHit[8];
								$detailedHit[8] = $detailedHit[6];
								$detailedHit[6] = $exchange;
								$exchange = $detailedHit[9];
								$detailedHit[9] = $detailedHit[7];
								$detailedHit[7] = $exchange;
								$exchange = $detailedHit[1];
								$detailedHit[1] = $detailedHit[0];
								$detailedHit[0] = $exchange;
							}
							else
							{
								my $exchange = $detailedHit[8];
								$detailedHit[8] = $detailedHit[7];
								$detailedHit[7] = $exchange;
								$exchange = $detailedHit[9];
								$detailedHit[9] = $detailedHit[6];
								$detailedHit[6] = $exchange;
								$exchange = $detailedHit[1];
								$detailedHit[1] = $detailedHit[0];
								$detailedHit[0] = $exchange;
							}

							if($detailedHit[6] == 1 || $detailedHit[7] == $assemblySequenceLength->{$detailedHit[0]})
							{
								$goodOverlap = 1;
							}
							my $reverseBlast = join "\t",@detailedHit;
							push @alignments, $reverseBlast;							
						}									
					}
					close(CMDA);
					if($goodOverlap)
					{
						foreach (@alignments)
						{
							my @detailedHit = split("\t",$_);
							#write to alignment
							my $insertAlignment=$dbh->prepare("INSERT INTO alignment VALUES ('', 'SEQtoSEQ\_1e-200\_$identitySeqToSeq\_$minOverlapSeqToSeq', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)");
							$insertAlignment->execute(@detailedHit);
						}
					}
				}
			}
		}
		close(CMD);
		unlink("$commoncfg->{TMPDIR}/$assembly[4].$$.seq");
		unlink("$commoncfg->{TMPDIR}/$assembly[4].$$.new.seq");
		unlink("$commoncfg->{TMPDIR}/$assembly[4].$$.seq.nhr");
		unlink("$commoncfg->{TMPDIR}/$assembly[4].$$.seq.nin");
		unlink("$commoncfg->{TMPDIR}/$assembly[4].$$.seq.nsq");
		`rm $commoncfg->{TMPDIR}/*.aln.html`; #delete cached files
		foreach my $queryId (keys %$goodSequenceId)
		{
			unlink("$commoncfg->{TMPDIR}/$queryId.$$.seq");
			foreach my $subjectId (keys %{$goodSequenceId->{$queryId}})
			{
				unlink("$commoncfg->{TMPDIR}/$subjectId.$$.seq");
			}
		}

		my $updateAssemblyToWork=$dbh->do("UPDATE matrix SET barcode = '1' WHERE id = $assemblyId");
	}
	else{
		die "couldn't fork: $!\n";
	} 
}
else
{
	print <<END;
<script>
	parent.errorPop("Please give an assembly id!");
</script>	
END
}