use anchor_lang::prelude::*;

declare_id!("51xxfcg4q4JS1b6dtFg6KdCSYFsTZCp3J696HK1Nsq2t"); // Replace with your actual program ID after deployment

#[program]
pub mod grievance_portal {
    use super::*;

    pub fn register_grievance(ctx: Context<RegisterGrievance>,cid: String,message_hash: [u8; 32],department: String,) -> Result<()> {
        let grievance = &mut ctx.accounts.grievance;
        let clock = Clock::get()?;

        require!(cid.len() > 0 && cid.len() <= 64, GrievanceError::InvalidCID);
        require!(department.len() > 0 && department.len() <= 32, GrievanceError::InvalidDepartment);

        // Initialize grievance account
        grievance.cid = cid;
        grievance.message_hash = message_hash;
        grievance.department = department;
        grievance.created_at = clock.unix_timestamp;
        grievance.created_by = ctx.accounts.user.key();
        grievance.bump = ctx.bumps.grievance;

        msg!("Grievance registered successfully");
        msg!("CID: {}", grievance.cid);
        msg!("Department: {}", grievance.department);
        msg!("Timestamp: {}", grievance.created_at);

        Ok(())
    }

    pub fn update_status(ctx: Context<UpdateStatus>,seed: u64,status: GrievanceStatus,note_cid: Option<String>,
    ) -> Result<()> {
        let status_update = &mut ctx.accounts.status_update;
        let clock = Clock::get()?;

        // Validate note CID if provided
        if let Some(ref cid) = note_cid {
            require!(cid.len() > 0 && cid.len() <= 64, GrievanceError::InvalidCID);
        }

        // Initialize status update account
        status_update.grievance = ctx.accounts.grievance.key();
        status_update.status = status.clone();
        status_update.note_cid = note_cid;
        status_update.updated_by = ctx.accounts.updater.key();
        status_update.updated_at = clock.unix_timestamp;
        status_update.bump = ctx.bumps.status_update;

        msg!("📝 Status update created");
        msg!("📋 Grievance: {}", status_update.grievance);
        msg!("🔄 Status: {:?}", status);
        msg!("👤 Updated by: {}", ctx.accounts.updater.key());
        msg!("⏰ Timestamp: {}", status_update.updated_at);
        msg!("🔢 Seed: {}", seed);

        Ok(())
    }
}


#[account]
pub struct Grievance {
    pub cid: String,                // IPFS CID of encrypted grievance data (max 64 chars)
    pub message_hash: [u8; 32],     // SHA-256 hash of plaintext message
    pub department: String,         // Department (max 32 chars)
    pub created_at: i64,            // Unix timestamp
    pub created_by: Pubkey,         // User who created the grievance
    pub bump: u8,                   // PDA bump seed
}

#[account]
pub struct StatusUpdate {
    pub grievance: Pubkey,          // Reference to parent grievance
    pub status: GrievanceStatus,    // Status value
    pub note_cid: Option<String>,   // Optional IPFS CID for update notes (max 64 chars)
    pub updated_by: Pubkey,         // Who updated the status
    pub updated_at: i64,            // Unix timestamp
    pub bump: u8,                   // PDA bump seed
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq, Debug)]
pub enum GrievanceStatus {
    Pending,      // Initial state
    InProgress,   // Being worked on
    Resolved,     // Successfully resolved
    Rejected,     // Rejected/Invalid
}



#[derive(Accounts)]
#[instruction(cid: String, message_hash: [u8; 32], department: String)]
pub struct RegisterGrievance<'info> {
    #[account(init,payer = user,space = Grievance::SPACE,seeds = [b"grievance",user.key().as_ref(),message_hash.as_ref(),],bump)]
    pub grievance: Account<'info, Grievance>,

    #[account(mut)]
    pub user: Signer<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(seed: u64, status: GrievanceStatus, note_cid: Option<String>)]
pub struct UpdateStatus<'info> {
    /// The original grievance (read-only, just for reference)
    pub grievance: Account<'info, Grievance>,

    /// New status update account - creates a chain of updates
    /// Uses a client-provided seed (incrementing number) for uniqueness
    #[account(init,payer = updater,space = StatusUpdate::SPACE,seeds = [b"status",grievance.key().as_ref(),&seed.to_le_bytes(),],bump)]
    pub status_update: Account<'info, StatusUpdate>,

    #[account(mut)]
    pub updater: Signer<'info>,

    pub system_program: Program<'info, System>,
}


impl Grievance {
    // Calculate account size
    // 8 (discriminator) + 
    // 4 + 64 (cid String) + 
    // 32 (message_hash) + 
    // 4 + 32 (department String) + 
    // 8 (created_at) + 
    // 32 (created_by Pubkey) + 
    // 1 (bump)
    pub const SPACE: usize = 8 + 68 + 32 + 36 + 8 + 32 + 1;
}

impl StatusUpdate {
    // Calculate account size
    // 8 (discriminator) + 
    // 32 (grievance Pubkey) + 
    // 1 (status enum) + 
    // 1 + 4 + 64 (Option<String> note_cid) + 
    // 32 (updated_by Pubkey) + 
    // 8 (updated_at) + 
    // 1 (bump)
    pub const SPACE: usize = 8 + 32 + 1 + 69 + 32 + 8 + 1;
}


#[error_code]
pub enum GrievanceError {
    #[msg("Invalid IPFS CID format or length")]
    InvalidCID,

    #[msg("Invalid department name")]
    InvalidDepartment,
}
